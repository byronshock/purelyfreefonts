// 52-owned-parse: the hopper's parser (Milestone 3, slice 1; site/CONTRACT.md sections 5
// and 11). A port of tff_catalog.owned.parse: a dropped file (bytes) or a paste (text), in
// any format of docs/owned-fonts.md, to entries, warnings and error codes. It never throws
// on a string or bytes. tests/vectors/owned-fonts.json is the source of truth, and
// tests/site/test_owned_js.py runs it in Chromium and Firefox and compares random inputs with
// Python. Pure code: no page, network, storage or clock, so a list stays in this tab's memory.
//
// Lengths and limits count code points, as Python's len() does, never UTF-16 units; only
// MAX_BYTES and the UTF-16 sniff count bytes. Every pattern is ASCII, so /i without the u
// flag matches an ASCII letter in its two ASCII cases only, as Python's re.ASCII |
// re.IGNORECASE does. Line patterns use the s flag, because a line may hold U+2028. Lone
// surrogates become U+FFFD with the warning 'decoding'; /\p{Cs}/u matches only lone ones.
// No regex reads a JSON string of unbounded length: a backtracking regex keeps state per
// character and overflows its stack past about 2^23 characters, so loops read them.
const OwnedParse = (() => {
  const LIMITS = Object.freeze({
    max_bytes: 10000000,
    max_chars: 10000000,
    max_line: 2000,
    max_name: 200,
    sniff_bytes: 4096,
    sniff_html: 1024,
    sniff_json: 4096,
    few_names: 20,
    cut_off_lines: 500,
    fc_list_one_in: 20,
  });
  const FORMATS = Object.freeze([
    'macos-json', 'macos-text', 'fc-list', 'fc-list-plain', 'windows-registry', 'json-list',
    'lines',
  ]);
  const ERRORS = Object.freeze([
    'empty', 'too_big', 'binary', 'font_file', 'html', 'command', 'not_found', 'no_names',
  ]);
  const WARNINGS = Object.freeze([
    'few_names', 'cut_off', 'prompt_dropped', 'decoding', 'paths_dropped', 'hidden_skipped',
  ]);
  // The commands of docs/owned-fonts.md and their parts, in ASCII lower case.
  const COMMAND_WORDS = Object.freeze([
    'add-type', 'fc-list', 'fc-match', 'fc-query', 'foreach-object', 'get-childitem',
    'get-item', 'get-itemproperty', 'new-object', 'osascript', 'pbcopy', 'powershell',
    'powershell.exe', 'pwsh', 'pwsh.exe', 'set-clipboard', 'system_profiler', 'wl-copy', 'xclip',
  ]);
  const FONT_EXTENSIONS = Object.freeze([
    'fnt', 'fon', 'otc', 'otf', 'pfb', 'pfm', 'ttc', 'ttf', 'woff', 'woff2',
  ]);
  // keys.py's WHITESPACE_CODEPOINTS and JOINER_CODEPOINTS: Keys.DROP_CODEPOINTS holds white
  // space, then dashes, the underscore and joiners.
  const WHITESPACE = Object.freeze(Keys.DROP_CODEPOINTS.slice(0, 11));
  const JOINERS = Object.freeze(Keys.DROP_CODEPOINTS.slice(18));

  const COMMANDS = new Set(COMMAND_WORDS);
  const EXTENSIONS = new Set(FONT_EXTENSIONS);
  const ALNUM = new Set('0123456789abcdefghijklmnopqrstuvwxyz');
  const FONT_MAGIC = ['\x00\x01\x00\x00', 'OTTO', 'true', 'ttcf', 'wOFF', 'wOF2'];
  const BINARY_MAGIC = ['PK\x03\x04', 'PK\x05\x06', '%PDF-', '\x89PNG\r\n\x1a\n', '\x1f\x8b'];

  const codes = (entries) =>
    entries.flatMap((entry) => {
      const [first, last = first] = entry.split('-').map((hex) => parseInt(hex, 16));
      return Array.from({ length: last - first + 1 }, (_, i) => first + i);
    });
  const classOf = (points) => points.map((cp) => `\\u{${cp.toString(16)}}`).join('');
  const BLANK = new Set([...codes(WHITESPACE), ...codes(JOINERS)]);
  const WS = new RegExp(`[${classOf(codes(WHITESPACE))}]`, 'gu');
  const VISIBLE = new RegExp(`[^${classOf([...BLANK])}]`, 'u');
  const PRINTABLE = /^[\x20-\x7e]*$/;
  const ASCII = /^[\x00-\x7f]*$/;
  const CONTROL = /[\x00-\x08\x0e-\x1f\x7f]/g;
  const HAS_CONTROL = /[\x00-\x08\x0e-\x1f\x7f]/;
  const LONE = /\p{Cs}/u;
  const LONE_ALL = /\p{Cs}/gu;
  const LINE_BREAK = /\r\n|\r|\n/;
  const HTML = /<!doctype html|<html[ \t\r\n>]|<head[ \t\r\n>]|<body[ \t\r\n>]/i;
  // JSON: the first visible character opens it, or (a paste cut at the top) most of the first
  // lines are JSON members, strings or brackets (jsonLine). JSON_MEMBER only ever reads the
  // first SNIFF_JSON code points, so its backtracking stays small.
  const JSON_MEMBER = /"(?:[^"\\]|\\.)*"[ \t\r\n]*:/s;
  const JSON_ARRAY = /^\[[ \t\r\n]*"/;
  // JSON's white space, and the characters that end a token that is no string, compared
  // directly: a Set lookup per character costs more.
  const jsonSpace = (u) => u === 0x20 || u === 0x0a || u === 0x0d || u === 0x09;
  const jsonStop = (u) =>
    jsonSpace(u) || u === 0x7b || u === 0x7d || u === 0x5b || u === 0x5d || u === 0x3a || u === 0x2c || u === 0x22;
  const FAMILY_LINE = /^[ \t]*Family:[ \t]*(.*)$/s;
  const LOCATION_LINE = /^[ \t]*Location:/;
  // The patterns below run on cleaned lines: single spaces, no tabs, no controls.
  const DASH_RULE = /^-{3,}(?: -{3,})*$/;
  const PATH = /^(?:\/|~|[A-Za-z]:\\)/;
  const REGISTRY_SUFFIX =
    /\((?:TrueType|OpenType|Type 1|All res|VGA res|8514\/a res|[0-9,]+ res)\)(?: ?:.*)?$/iys;
  const TABLE_KEY = /^Fonts[ \t]{2,}/;
  const PS_PROPERTY = /^PS(?:Path|ParentPath|ChildName|Drive|Provider) ?:/;
  const NOT_FOUND =
    /command not found|is not recognized as|commandnotfoundexception|unknown command/i;
  // PowerShell's error record and fish's caret line, after a not-found line.
  const ERROR_DETAIL = /^(?:\+ |At line:[0-9]|At char:[0-9])/;
  const CARETS = /^\^[~^]*$/;
  // A prompt: PowerShell's "PS C:\...>", "[user@host dir]$", "user@host:~$", "user@host ~ %"
  // or "~/dir $", then the rest of the line (maybe a command). A bare prompt symbol counts
  // alone or before a command, so names that start or end with $, #, % or > are kept.
  const PROMPT = new RegExp(
    '^(?:PS(?: [^>]*)?>|\\[[^\\]]*\\] ?[$#%>\u276f]' +
      '|[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+(?:[: ][^ $#%>\u276f]*)? ?[$#%>\u276f]' +
      '|~[^ $#%>\u276f]* ?[$#%>\u276f]) ?(.*)$',
    's',
  );
  const BARE_PROMPT = /^[$#%>\u276f] (.+)$/s;
  const LONE_PROMPT = new Set(['$', '#', '%', '>', '\u276f']);
  // A command's first word, after "(" or "&" and an optional sudo or doas.
  const COMMAND = /^(?:[(&] ?)*(?:(?:sudo|doas) )?([^ :|>;&()]+)/i;

  // On ASCII, toLowerCase changes A to Z only; on other text it would lower other letters too.
  const asciiLower = (s) =>
    ASCII.test(s) ? s.toLowerCase() : s.replace(/[A-Z]+/g, (m) => m.toLowerCase());

  const isLow = (unit) => unit >= 0xdc00 && unit <= 0xdfff;
  const isHigh = (unit) => unit >= 0xd800 && unit <= 0xdbff;

  // The number of code points in s.
  const length = (s) => {
    let n = s.length;
    for (let i = 0; i + 1 < s.length; i++) {
      if (isHigh(s.charCodeAt(i)) && isLow(s.charCodeAt(i + 1))) {
        n -= 1;
        i += 1;
      }
    }
    return n;
  };
  const longer = (s, max) => s.length > max && length(s) > max;

  // The first n code points of s.
  const head = (s, n) => {
    if (s.length <= n) return s;
    let end = 0;
    for (let count = 0; count < n && end < s.length; count++) {
      end += isHigh(s.charCodeAt(end)) && isLow(s.charCodeAt(end + 1)) ? 2 : 1;
    }
    return s.slice(0, end);
  };

  // VISIBLE.test(s), without the regex where an ASCII character settles it: ASCII's only blank
  // characters are \t to \r and the space.
  const visible = (s) => {
    for (let i = 0; i < s.length; i++) {
      const unit = s.charCodeAt(i);
      if (unit >= 0x80) return VISIBLE.test(s.slice(i));
      if (unit !== 0x20 && (unit < 0x09 || unit > 0x0d)) return true;
    }
    return false;
  };

  // s without the blank characters (white space and joiners) at its ends, in linear time.
  const lstrip = (s) => {
    let i = 0;
    while (i < s.length && BLANK.has(s.charCodeAt(i))) i += 1;
    return s.slice(i);
  };
  const strip = (s) => {
    let j = s.length;
    while (j > 0 && BLANK.has(s.charCodeAt(j - 1))) j -= 1;
    return lstrip(s.slice(0, j));
  };
  const rstripSpaces = (s) => {
    let j = s.length;
    while (j > 0 && s[j - 1] === ' ') j -= 1;
    return s.slice(0, j);
  };
  const collapse = (s) => s.split(' ').filter(Boolean).join(' ');

  // Controls dropped, lone surrogates replaced and white space collapsed; '' when nothing
  // visible is left (white space and joiners only).
  const clean = (text) => {
    if (PRINTABLE.test(text)) {
      const spaced = text.includes('  ') || text[0] === ' ' || text[text.length - 1] === ' ';
      return spaced ? collapse(text) : text;
    }
    let out = text.replace(CONTROL, '');
    if (LONE.test(out)) out = out.replace(LONE_ALL, '\ufffd');
    out = collapse(out.replace(WS, ' '));
    return VISIBLE.test(out) ? out : '';
  };

  const entry = (families, full = null, postscript = null, trusted = false) => ({
    families,
    full,
    postscript,
    trusted,
  });

  const newState = (pasted) => ({
    pasted,
    warnings: new Set(),
    lines: 0,
    hidden: 0,
    tooLong: 0,
    dropped: 0,
  });

  // A kept name, or null for an empty, over-long or hidden one (counted).
  const nameOf = (state, raw) => {
    if (raw === null || raw === undefined) return null;
    if (!ASCII.test(raw) && LONE.test(raw)) state.warnings.add('decoding');
    const text = clean(raw);
    if (!text) return null;
    if (longer(text, LIMITS.max_name)) {
      state.tooLong += 1;
      return null;
    }
    if (text.startsWith('.')) {
      state.hidden += 1;
      return null;
    }
    return text;
  };

  // A sorted paste of 500 or more names whose first name starts after C: its top is missing.
  // Only first characters that are ASCII letters or digits count. Sorted: at least nine in ten
  // neighbouring pairs in order.
  const firstKey = (name) => {
    if (!ASCII.test(name)) return Keys.searchKey(name).charAt(0);
    // The ASCII key's first character, without building the key: \t to \r, space, hyphen and
    // underscore are dropped.
    for (let i = 0; i < name.length; i++) {
      const unit = name.charCodeAt(i);
      if (!((unit >= 0x09 && unit <= 0x0d) || unit === 0x20 || unit === 0x2d || unit === 0x5f)) {
        return name[i].toLowerCase();
      }
    }
    return '';
  };
  const cutOff = (entries) => {
    const firsts = [];
    for (const item of entries) {
      const first = firstKey(item.families.length ? item.families[0] : item.full || '');
      if (ALNUM.has(first)) {
        if (!firsts.length && first < 'd') return false;
        firsts.push(first);
      }
    }
    if (firsts.length < LIMITS.cut_off_lines) return false;
    let inOrder = 0;
    for (let i = 1; i < firsts.length; i++) if (firsts[i - 1] <= firsts[i]) inOrder += 1;
    return 10 * inOrder >= 9 * (firsts.length - 1) && firsts[0] >= 'd';
  };

  const done = (state, entries, error = null) => {
    const seen = new Set();
    const unique = [];
    for (const [kind, item] of entries) {
      // Names hold no C0 control (clean drops them), so \x01 and \x02 can't occur in one.
      const full = item.full === null ? '\x02' : item.full;
      const postscript = item.postscript === null ? '\x02' : item.postscript;
      const id = `${kind}\x00${item.families.join('\x01')}\x00${full}\x00${postscript}\x00${item.trusted}`;
      if (!seen.has(id)) {
        seen.add(id);
        unique.push([kind, item]);
      }
    }
    const kinds = new Map();
    for (const [kind] of unique) kinds.set(kind, (kinds.get(kind) || 0) + 1);
    const count = (kind) => kinds.get(kind) || 0;
    const format = unique.length
      ? FORMATS.reduce((best, kind) => (count(kind) > count(best) ? kind : best))
      : null;
    const failed = error || (unique.length ? null : 'no_names');
    if (unique.length > 0 && unique.length < LIMITS.few_names) state.warnings.add('few_names');
    if (state.hidden) state.warnings.add('hidden_skipped');
    if (
      state.pasted &&
      ['macos-text', 'fc-list', 'windows-registry', 'lines'].includes(format) &&
      cutOff(unique.filter(([kind]) => kind === format).map(([, item]) => item))
    ) {
      state.warnings.add('cut_off');
    }
    return {
      format: failed ? null : format,
      error: failed,
      warnings: WARNINGS.filter((warning) => state.warnings.has(warning)),
      counts: {
        lines: state.lines,
        entries: failed ? 0 : unique.length,
        hidden: state.hidden,
        too_long: state.tooLong,
        dropped: state.dropped,
      },
      entries: failed ? [] : unique.map(([, item]) => item),
    };
  };

  const startsWith = (bytes, magic) =>
    bytes.length >= magic.length && Array.from(magic).every((c, i) => bytes[i] === c.charCodeAt(0));

  // UTF-16 without a byte-order mark: two or more NUL bytes, nine in ten on one side of the
  // pairs. Line breaks alone give the pattern, so a list of CJK names is caught too.
  const utf16Guess = (sample) => {
    let even = 0;
    let odd = 0;
    for (let i = 0; i < sample.length; i++) {
      if (sample[i] === 0) {
        if (i % 2) odd += 1;
        else even += 1;
      }
    }
    if (even + odd < 2) return null;
    if (odd >= 9 * even) return 'utf-16le';
    if (even >= 9 * odd) return 'utf-16be';
    return null;
  };

  // The text of the bytes and whether a replacement character went in.
  const decode = (bytes) => {
    let codec = 'utf-8';
    let start = 0;
    if (startsWith(bytes, '\xef\xbb\xbf')) start = 3;
    else if (startsWith(bytes, '\xff\xfe')) [codec, start] = ['utf-16le', 2];
    else if (startsWith(bytes, '\xfe\xff')) [codec, start] = ['utf-16be', 2];
    else codec = utf16Guess(bytes.subarray(0, LIMITS.sniff_bytes)) || 'utf-8';
    const body = bytes.subarray(start);
    try {
      return [new TextDecoder(codec, { fatal: true, ignoreBOM: true }).decode(body), false];
    } catch (error) {
      return [new TextDecoder(codec, { ignoreBOM: true }).decode(body), true];
    }
  };

  // A NUL, or C0 controls (but white space) and DEL over 1% of the code points.
  const binary = (text) => {
    if (!HAS_CONTROL.test(text)) return false; // most texts: no loop over every unit
    let controls = 0;
    for (let i = 0; i < text.length; i++) {
      const unit = text.charCodeAt(i);
      if (unit === 0) return true;
      if (unit <= 8 || (unit >= 0x0e && unit <= 0x1f) || unit === 0x7f) controls += 1;
    }
    return controls > 0 && controls * 100 > length(text);
  };

  // --- JSON (system_profiler -json SPFontsDataType, or a list of names) ---

  // The index just past the JSON string that opens at text[i] (a '"'), or -1 when the text
  // ends first or, unless raw, a control character (U+0000 to U+001F) comes first. owned.py's
  // "(?:[^"\\]|\\.)*", where a backslash takes the next character, whatever it is.
  const stringEnd = (text, i, raw) => {
    for (let j = i + 1; j < text.length; j++) {
      const unit = text.charCodeAt(j);
      if (unit === 0x22) return j + 1;
      if (unit === 0x5c) j += 1;
      else if (unit < 0x20 && !raw) return -1;
    }
    return -1;
  };

  // owned.py's _JSON_LINE: spaces or tabs, then a string with a ":" and anything after it,
  // or a string or a bracket with an optional ",", then spaces or tabs.
  const jsonLine = (line) => {
    const blank = (c) => c === ' ' || c === '\t';
    let i = 0;
    while (blank(line[i])) i += 1;
    const c = line[i];
    if (c === '"') {
      i = stringEnd(line, i, true);
      if (i < 0) return false;
      while (blank(line[i])) i += 1;
      if (line[i] === ':') return true;
    } else if (c === '[' || c === ']' || c === '{' || c === '}') {
      i += 1;
    } else {
      return false;
    }
    if (line[i] === ',') i += 1;
    while (blank(line[i])) i += 1;
    return i === line.length;
  };

  const jsonLike = (body, lines) => {
    if (
      (body[0] === '{' || body[0] === '[') &&
      (JSON_MEMBER.test(head(body, LIMITS.sniff_json)) || JSON_ARRAY.test(body))
    ) {
      return true;
    }
    const first = [];
    for (let i = 0; i < lines.length && i < 200 && first.length < 50; i++) {
      if (VISIBLE.test(lines[i])) first.push(lines[i]);
    }
    return first.length >= 3 && 2 * first.filter(jsonLine).length >= first.length;
  };

  // A string token's value, or null when JSON can't read it. A token without a backslash
  // holds no escape and (stringEnd saw to it) no control character: its value is its inside.
  const jsonString = (token) => {
    if (!token.includes('\\')) return token.slice(1, -1);
    try {
      return JSON.parse(token);
    } catch (error) {
      return null;
    }
  };

  // A reader of text's JSON tokens, one per call and null at the end (owned.py's
  // _json_tokens): a closed string, one of {}[]:, a run of other characters, or a lone '"'
  // that opens no closed string, where the scan stops. JSON's white space between tokens is
  // skipped. Tokens are read only as the scan asks, so a run of escaped quotes after an
  // unclosed string is read once, not once per quote.
  const jsonTokens = (text) => {
    let at = 0;
    return () => {
      while (at < text.length && jsonSpace(text.charCodeAt(at))) at += 1;
      if (at >= text.length) return null;
      const start = at;
      const c = text[at];
      if (c === '"') {
        const end = stringEnd(text, at, false);
        at = end < 0 ? text.length : end;
        return end < 0 ? '"' : text.slice(start, end);
      }
      at += 1;
      if (c === '{' || c === '}' || c === '[' || c === ']' || c === ':' || c === ',') return c;
      while (at < text.length && !jsonStop(text.charCodeAt(at))) at += 1;
      return text.slice(start, at);
    };
  };

  // The objects (as maps of their string members) and listed strings of a JSON text, whole,
  // cut or broken, in opening order, and whether it was cut (owned.py's _scan_json, which
  // both sides use for every JSON text: JSON.parse and json.loads differ in their limits and
  // keep only the last of duplicate keys). Members before the first "{" (a paste cut at the
  // top) belong to an implicit object, which a stray "}" closes; strings there count as
  // listed. A member whose value is no string removes the key, as a later duplicate would.
  const scanJson = (text) => {
    const read = jsonTokens(text);
    let held; // a token read ahead, or undefined
    const peek = () => (held === undefined ? (held = read()) : held);
    const next = () => {
      const token = peek();
      held = undefined;
      return token;
    };
    const root = new Map();
    const out = [root];
    const strings = [];
    const stack = [root]; // null marks an array
    const innermost = () => stack[stack.length - 1];
    let cut = false;
    for (let token = next(); token !== null; token = next()) {
      if (token === '{') {
        const object = new Map();
        out.push(object);
        stack.push(object);
      } else if (token === '[') {
        stack.push(null);
      } else if (token === '}') {
        while (stack.length > 1 && innermost() === null) stack.pop();
        if (stack.length > 1) {
          stack.pop();
        } else {
          cut = true;
          stack[0] = new Map();
          out.push(stack[0]);
        }
      } else if (token === ']') {
        if (stack.length > 1 && innermost() === null) stack.pop();
      } else if (token === '"') {
        cut = true; // an unclosed string: the text ends inside it
        break;
      } else if (token[0] === '"' && peek() === ':') {
        next();
        const object = innermost();
        const key = jsonString(token);
        if (object !== null && key !== null) {
          const value = peek();
          if (value !== null && value.length > 1 && value[0] === '"') {
            next();
            const decoded = jsonString(value);
            if (decoded === null) object.delete(key);
            else object.set(key, decoded);
          } else {
            object.delete(key);
          }
        }
      } else if (token[0] === '"' && (innermost() === null || stack.length === 1)) {
        const decoded = jsonString(token);
        if (decoded !== null) strings.push(decoded);
      }
    }
    const objects = out.filter((object) => object.size > 0);
    return [objects, strings, cut || stack.length > 1 || root.size > 0];
  };

  const parseJson = (text, state) => {
    const [objects, strings, cut] = scanJson(text);
    if (cut) state.warnings.add('cut_off');
    const entries = [];
    for (const object of objects) {
      if (typeof object.get('path') === 'string') state.warnings.add('paths_dropped');
      const family = object.get('family');
      if (typeof family !== 'string') continue;
      const name = nameOf(state, family);
      if (name === null) continue;
      const full = object.get('fullname');
      const postscript = object.get('_name');
      entries.push([
        'macos-json',
        entry(
          [name],
          typeof full === 'string' ? nameOf(state, full) : null,
          typeof postscript === 'string' ? nameOf(state, postscript) : null,
          true,
        ),
      ]);
    }
    if (!entries.length) {
      // A list of names (ConvertTo-Json of a one-per-line command, for example).
      for (const item of strings) {
        const name = nameOf(state, item);
        if (name !== null) entries.push(['json-list', entry([name])]);
      }
    }
    return done(state, entries);
  };

  // --- system_profiler text, fc-list, the registry and one name per line ---

  const parseMacosText = (lines, state) => {
    const entries = [];
    for (const line of lines) {
      const m = longer(line, LIMITS.max_line) ? false : FAMILY_LINE.exec(line);
      if (m === false) {
        state.tooLong += 1;
      } else if (m !== null) {
        const name = nameOf(state, m[1]);
        if (name !== null) entries.push(['macos-text', entry([name], null, null, true)]);
      } else if (LOCATION_LINE.test(line)) {
        state.warnings.add('paths_dropped');
      } else if (VISIBLE.test(line)) {
        state.dropped += 1;
      }
    }
    return done(state, entries);
  };

  // Text split at fontconfig's commas and unescaped, and whether fontconfig wrote it. A comma
  // splits unless a backslash escapes it or a space follows it (fontconfig never writes ", ").
  // \\, \, \: and \- are fontconfig's escapes; any other backslash stays.
  const splitFc = (text) => {
    if (!text.includes(',') && !text.includes('\\')) return [[text], false];
    const parts = [];
    let current = '';
    let written = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (c === '\\' && i + 1 < text.length && '\\,:-'.includes(text[i + 1])) {
        current += text[i + 1];
        written = true;
        i += 1;
      } else if (c === ',' && text[i + 1] !== ' ') {
        parts.push(current);
        current = '';
        written = true;
      } else {
        current += c;
      }
    }
    parts.push(current);
    return [parts, written];
  };

  // Text up to its first sep that no backslash escapes.
  const beforeUnescaped = (text, sep) => {
    for (let i = 0; i < text.length; i++) {
      if (text[i] === '\\') i += 1;
      else if (text[i] === sep) return text.slice(0, i);
    }
    return text;
  };

  const LONGEST_COMMAND = Math.max(...COMMAND_WORDS.map((word) => word.length));
  const isCommand = (line) => {
    const m = COMMAND.exec(line);
    return m !== null && m[1].length <= LONGEST_COMMAND && COMMANDS.has(asciiLower(m[1]));
  };

  // The value name of a registry line ("Arial (TrueType)", "Odd Name : odd.ttf"), or null.
  // The suffix is found from the last "(" backwards, so a line costs linear time.
  const registryName = (line, state) => {
    for (let i = line.lastIndexOf('('); i > 0; i = line.lastIndexOf('(', i - 1)) {
      REGISTRY_SUFFIX.lastIndex = i;
      if (REGISTRY_SUFFIX.test(line)) {
        const data = line.slice(line.indexOf(')', i));
        if (data.includes('\\') || data.includes('/')) state.warnings.add('paths_dropped');
        return rstripSpaces(line.slice(0, i));
      }
    }
    const at = line.indexOf(' : ');
    if (at > 0) {
      const data = line.slice(at + 3);
      if (EXTENSIONS.has(asciiLower(data.slice(data.lastIndexOf('.') + 1)))) {
        if (data.includes('\\') || data.includes('/')) state.warnings.add('paths_dropped');
        return line.slice(0, at);
      }
    }
    return null;
  };

  // The kept names of one font, or null when its first name is hidden or none is kept.
  const families = (state, raw) => {
    if (raw.length && clean(raw[0]).startsWith('.')) {
      state.hidden += 1;
      return null;
    }
    const names = [];
    for (const part of raw) {
      const name = nameOf(state, part);
      if (name !== null && !names.includes(name)) names.push(name);
    }
    return names.length ? names : null;
  };

  const spaces = (s) => s.split(' ').length - 1;

  // fc-list (comma-joined or plain), the registry, or one name per line (owned.py's
  // _parse_lines, which says which lines are dropped and when a list is fc-list's).
  const parseLines = (lines, state) => {
    const items = []; // [kind, payload], or null once dropped
    let previous = null; // the item of the previous visible line
    let notFound = false;
    let command = false;
    for (const raw of lines) {
      if (longer(raw, LIMITS.max_line)) {
        state.tooLong += 1;
        previous = null;
        continue;
      }
      const stripped = strip(raw);
      const table = TABLE_KEY.exec(stripped);
      const line = clean(table ? stripped.slice(table[0].length) : stripped);
      if (!line) continue;
      let here = null;
      let m = null;
      // A pattern is tried only where the line's first character, or an "@" for a prompt
      // with a user name, lets it match.
      const c0 = line[0];
      if (c0 === '-' && DASH_RULE.test(line)) {
        // A PowerShell table rule: the line above it, with as many words, is the header.
        const item = previous === null ? null : items[previous];
        if (item !== null && item[0] === 'plain' && spaces(item[1]) === spaces(line)) {
          items[previous] = null;
          state.dropped += 1;
        }
        state.dropped += 1;
      } else if (
        line.startsWith('Hive:') ||
        (c0 === 'P' && PS_PROPERTY.test(line)) ||
        ((c0 === '+' || c0 === 'A') && ERROR_DETAIL.test(line)) ||
        (c0 === '^' && CARETS.test(line)) ||
        line.includes('Check the spelling of the name')
      ) {
        state.dropped += 1;
      } else if (NOT_FOUND.test(line)) {
        notFound = true;
        state.dropped += 1;
      } else if (
        ((c0 === 'P' || c0 === '[' || c0 === '~' || line.includes('@')) &&
          (m = PROMPT.exec(line)) !== null) ||
        (line.length === 1 && LONE_PROMPT.has(line))
      ) {
        state.warnings.add('prompt_dropped');
        command = command || (m !== null && isCommand(m[1]));
        state.dropped += 1;
      } else if (LONE_PROMPT.has(c0) && (m = BARE_PROMPT.exec(line)) !== null && isCommand(m[1])) {
        state.warnings.add('prompt_dropped');
        command = true;
        state.dropped += 1;
      } else if (isCommand(line)) {
        command = true;
        state.dropped += 1;
      } else if (PATH.test(line)) {
        // Plain fc-list: "path: Family,Other:style=Bold"; the path goes at once.
        state.warnings.add('paths_dropped');
        const at = line.indexOf(': ');
        if (at >= 0) {
          here = items.length;
          items.push(['fc-list-plain', splitFc(beforeUnescaped(line.slice(at + 2), ':'))[0]]);
        } else {
          state.dropped += 1;
        }
      } else {
        const name = registryName(line, state);
        here = items.length;
        items.push(name === null ? ['plain', line] : ['windows-registry', name.split(' & ')]);
      }
      previous = here;
    }
    // In a list with at least as many registry lines as plain ones, a plain line is a value
    // name without a suffix ("Courier 10,12,15"), kept whole and untrusted: fontconfig never
    // writes a registry suffix, and this reading trusts nothing, so a tie counts too.
    // Otherwise a list is fc-list's (trusted) when at least one plain line in FC_LIST_ONE_IN
    // split at a fontconfig comma or held an escape.
    let registry = 0;
    let plain = 0;
    for (const item of items) {
      if (item !== null && item[0] === 'windows-registry') registry += 1;
      if (item !== null && item[0] === 'plain') plain += 1;
    }
    const valueNames = registry >= plain;
    let written = 0;
    for (const item of items) {
      if (item === null || item[0] !== 'plain') continue;
      if (valueNames) {
        item[0] = 'windows-registry';
        item[1] = item[1].split(' & ');
      } else {
        const [parts, byFontconfig] = splitFc(item[1]);
        item[1] = parts;
        if (byFontconfig) written += 1;
      }
    }
    const fcList = written > 0 && written * LIMITS.fc_list_one_in >= plain;
    const entries = [];
    for (const item of items) {
      if (item === null) continue;
      const [kind, payload] = item;
      if (kind === 'windows-registry') {
        for (const part of payload) {
          const name = nameOf(state, part);
          if (name !== null) entries.push(['windows-registry', entry([], name)]);
        }
        continue;
      }
      const names = families(state, payload);
      if (names === null) continue;
      if (kind === 'plain') {
        entries.push([fcList ? 'fc-list' : 'lines', entry(names, null, null, fcList)]);
      } else {
        entries.push(['fc-list-plain', entry(names, null, null, true)]);
      }
    }
    if (notFound && entries.length < LIMITS.few_names) return done(state, [], 'not_found');
    if (!entries.length && command) return done(state, [], 'command');
    return done(state, entries);
  };

  // The checks, in owned.py parse's order: size, font and binary magic numbers, encoding,
  // controls, nothing visible, HTML, then JSON, system_profiler text or line by line.
  // Anything else is 'empty'.
  const read = (input) => {
    const pasted = typeof input === 'string';
    const state = newState(pasted);
    let text;
    let replaced;
    if (input instanceof ArrayBuffer || ArrayBuffer.isView(input)) {
      const bytes =
        input instanceof ArrayBuffer
          ? new Uint8Array(input)
          : new Uint8Array(input.buffer, input.byteOffset, input.byteLength);
      if (bytes.length > LIMITS.max_bytes) return done(state, [], 'too_big');
      const font = FONT_MAGIC.some((magic) => startsWith(bytes, magic));
      if (font && bytes.subarray(0, 12).includes(0)) return done(state, [], 'font_file');
      if (BINARY_MAGIC.some((magic) => startsWith(bytes, magic))) return done(state, [], 'binary');
      [text, replaced] = decode(bytes);
    } else if (pasted) {
      if (longer(input, LIMITS.max_chars)) return done(state, [], 'too_big');
      replaced = LONE.test(input);
      text = replaced ? input.replace(LONE_ALL, '\ufffd') : input;
    } else {
      return done(state, [], 'empty');
    }
    if (replaced) state.warnings.add('decoding');
    if (longer(text, LIMITS.max_chars)) return done(state, [], 'too_big');
    if (text[0] === '\ufeff') text = text.slice(1);
    if (binary(text)) return done(state, [], 'binary');
    if (!VISIBLE.test(text)) return done(state, [], 'empty');
    const body = lstrip(text);
    if (HTML.test(head(body, LIMITS.sniff_html))) return done(state, [], 'html');
    const lines = text.split(LINE_BREAK);
    state.lines = lines.reduce((n, line) => n + (visible(line) ? 1 : 0), 0);
    if (jsonLike(body, lines)) return parseJson(text, state);
    if (lines.some((line) => FAMILY_LINE.test(line))) return parseMacosText(lines, state);
    return parseLines(lines, state);
  };

  // Every successful match leaves its text in the engine's legacy RegExp statics (RegExp.input,
  // lastMatch, leftContext and the rest), and a sticky pattern its lastIndex, until the next
  // one: a match of nothing in nothing on the way out leaves '' in all of them, so no script
  // can read anything of a list there once parse returns.
  const NOTHING = /(?:)/;
  const forget = () => {
    REGISTRY_SUFFIX.lastIndex = 0;
    NOTHING.exec('');
  };

  // Entries from a dropped file (an ArrayBuffer or typed array) or a paste (a string):
  // {format, error, warnings, counts, entries}, as Python's Parsed.to_json() (read).
  const parse = (input) => {
    try {
      return read(input);
    } finally {
      forget();
    }
  };

  return Object.freeze({
    parse, LIMITS, FORMATS, ERRORS, WARNINGS, COMMAND_WORDS, FONT_EXTENSIONS, WHITESPACE, JOINERS,
  });
})();
