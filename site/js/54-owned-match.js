// 54-owned-match: the hopper's matcher (Milestone 3, slice 1; site/CONTRACT.md sections 5
// and 11). A port of tff_catalog.owned's Table and match: OwnedParse's entries to the catalog
// families they own and those they only nearly match, by the match file's keys and words.
// An old name counts as owned (M3-D3 a), a patched build counts as owned unless
// {patched: false} (M3-D4 a), and derived, related, sibling and bundle names never do
// (M3-D5 a). A name is looked up whole, then with one trailing style word or build suffix
// stripped at a time, longest first; never by prefix. tests/vectors/owned-fonts.json and
// owned-known-answers.json are the source of truth (tests/site/test_owned_js.py, in Chromium
// and Firefox). Pure code, like OwnedParse; the rules come from the file's words, so this
// part holds no copy of the style and suffix lists. A table holds only the file: what match
// works out about a list's names lives only while that call runs.
//
// Step 5 gives 10,000 names 200 ms with the CPU slowed 4x, the first time this code runs. So
// the walk down a name's candidates builds and hashes as few strings as it can: on ASCII text
// a candidate's key is a prefix of the name's, worked out once and found among the table's
// keys by a small hash (fill, find), and a test on a text's last character, a necessary
// condition, skips each rule that can't apply. No list's names are kept from call to call.
const OwnedMatch = (() => {
  const FORMAT = 'tff-match';
  const VERSION = 1;
  const HOW = Object.freeze(['family', 'postscript', 'build', 'rename', 'patched']);
  const WHY = Object.freeze(['ambiguous', 'patched', 'sibling', 'related', 'bundle']);
  const OUTCOMES = Object.freeze(['owned', 'near', 'known', 'ineligible', 'unknown']);
  // A code's letter as an index in HOW (an owned code) or in WHY (a near-match).
  const HOW_INDEX = new Map([['', 0], ['p', 1], ['b', 2], ['r', 3], ['n', 4]]);
  const WHY_INDEX = new Map([['n', 1], ['s', 2], ['x', 3], ['u', 4]]);
  const AMBIGUOUS = 0;
  const PATCHED = 1;
  const SAME_FONT = new Set(['', 'p', 'b', 'r']); // codes that name the font itself
  const LETTERS = 'pbrnusx';
  const LOWER_DIGIT = new Set('abcdefghijklmnopqrstuvwxyz0123456789');
  const ASCII = /^[\x00-\x7f]*$/;
  const PRINTABLE = /^[\x20-\x7e]+$/;
  const ASCII_DROP = /[\t-\r \-_]/g;
  const JOINS = /[-_]/g;
  const HEX = /^[0-9A-Fa-f]{1,6}$/;
  const INDEX = /^(?:0|[1-9][0-9]*)$/;
  const CAMEL = /[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+/g;
  // A token that CAMEL reads as one part, which is then the token itself.
  const ONE_PART = /^(?:[A-Z]?[a-z]+|[A-Z]+|[0-9]+)$/;
  const TOKEN_CHARS = /^[A-Za-z0-9_-]+$/;
  const LONE = /(\p{Cs})/u;
  const PARTS = Object.freeze([
    'whitespace', 'dashes', 'joiners', 'separators', 'style', 'modifiers', 'postscript',
    'abbreviations', 'variable', 'variable_joined', 'nerd', 'nerd_tokens', 'cased',
  ]);

  // A match file this reader refuses: shown as a failure, never as a result (section 11).
  const refuse = (why) => Object.assign(new Error(`OwnedMatch: ${why}`), { code: 'malformed' });

  // Keys.matchKey, with a fast path for ASCII: there NFKC changes nothing, casefold is
  // lower case, and the dropped characters are \t to \r, space, hyphen and underscore.
  const matchKey = (s) =>
    ASCII.test(s) ? s.toLowerCase().replace(ASCII_DROP, '') : Keys.matchKey(s);

  // NFKC, keeping a lone surrogate as Python and Keys do (Firefox would make it U+FFFD).
  const nfkc = (s) =>
    ASCII.test(s)
      ? s
      : LONE.test(s)
        ? s.split(LONE).map((piece, i) => (i % 2 ? piece : piece.normalize('NFKC'))).join('')
        : s.normalize('NFKC');

  // The words' parts, each a list of non-empty printable ASCII strings: on ASCII words, /i
  // without the u flag folds as Python's re.ASCII | re.IGNORECASE (it would fold é and É).
  const list = (words, name) => {
    const value = words[name];
    if (!Array.isArray(value) || !value.every((item) => typeof item === 'string' && PRINTABLE.test(item))) {
      throw refuse(`words.${name} must be a list of printable ASCII strings`);
    }
    return value;
  };
  // Hex code points and ranges ("0020", "2000-200A") as [first, last]: 1 to 6 hex digits,
  // upwards, at most U+10FFFF, no surrogates (owned.py's _points).
  const ranges = (entries, name) =>
    entries.map((entry) => {
      const [first, last = first, extra] = entry.split('-');
      if (!HEX.test(first) || !HEX.test(last) || extra !== undefined) {
        throw refuse(`words.${name}: bad code point ${entry}`);
      }
      const [from, to] = [parseInt(first, 16), parseInt(last, 16)];
      if (to > 0x10ffff || to < from || (from <= 0xdfff && to >= 0xd800)) {
        throw refuse(`words.${name}: bad range ${entry}`);
      }
      return [from, to];
    });
  const within = (cp, spans) => spans.some(([from, to]) => cp >= from && cp <= to);
  const hex = (cp) => `\\u{${cp.toString(16)}}`;
  const escape = (s) => s.replace(/[\\^$.*+?()[\]{}|/-]/g, '\\$&');
  const byLength = (items) => [...items].sort((a, b) => b.length - a.length);
  // A table of the printable ASCII characters that end one of the words, for the cheap test
  // on a text's last character; lower case only makes it hold their lower-case form.
  const lastChars = (words, lower = false) => {
    const table = new Uint8Array(128);
    for (const word of words) {
      const c = word.charCodeAt(word.length - 1);
      table[lower && c >= 0x41 && c <= 0x5a ? c | 0x20 : c] = 1;
    }
    return table;
  };

  // The rules of a words object (owned.py's _compile), refused as it refuses them. The
  // three character tables become one class, and a match is replaced as Python's
  // str.translate would: joiners beat dashes, which beat white space (the later update).
  const compile = (words) => {
    if (words === null || typeof words !== 'object' || Array.isArray(words)) {
      throw refuse('words must be an object');
    }
    const parts = {};
    for (const name of PARTS) parts[name] = list(words, name);
    const white = ranges(parts.whitespace, 'whitespace');
    const dashes = ranges(parts.dashes, 'dashes');
    const joiners = ranges(parts.joiners, 'joiners');
    const spans = [...white, ...dashes, ...joiners];
    // The separators and phrases make a pattern that ignores case: on printable ASCII, /i
    // without the u flag folds as Python's re.ASCII | re.IGNORECASE (it would fold é and É).
    const points = ranges(parts.separators, 'separators').map(([from]) => from);
    if (
      !points.length ||
      parts.separators.some((s) => s.includes('-')) ||
      points.some((cp) => cp < 0x20 || cp > 0x7e)
    ) {
      throw refuse('words.separators must be single printable ASCII code points');
    }
    const seps = points.map((cp) => String.fromCharCode(cp));
    const sep = `[${seps.map(escape).join('')}]`;
    const phrases = byLength(parts.nerd).map((phrase) => phrase.split(' ').filter(Boolean));
    if (!phrases.length || phrases.some((phrase) => !phrase.length)) {
      throw refuse('words.nerd must hold phrases');
    }
    const nerd = phrases.map((phrase) => phrase.map(escape).join(`${sep}*`)).join('|');
    const tails = [...byLength(parts.nerd_tokens), ...parts.cased];
    const lengths = parts.variable.map((word) => word.length);
    const style = new Set(parts.style);
    const span = ([from, to]) => (from === to ? hex(from) : `${hex(from)}-${hex(to)}`);
    const swap = (c) => {
      const cp = c.codePointAt(0);
      return within(cp, joiners) ? '' : within(cp, dashes) ? '-' : ' ';
    };
    // Whether the tables leave every printable ASCII character as it is (the real words do).
    const asciiKept = Array.from({ length: 95 }, (_, i) => String.fromCharCode(0x20 + i)).every(
      (c) => !within(c.charCodeAt(0), spans) || swap(c) === c,
    );
    return {
      tableRe: new RegExp(`[${spans.map(span).join('')}]`, 'gu'),
      swap,
      asciiKept,
      seps: new Set(seps),
      sepCodes: lastChars(seps),
      afterToken: new Set([...LOWER_DIGIT, ...seps]),
      style,
      psStyle: new Set([...style, ...parts.postscript]),
      modifiers: new Set(parts.modifiers),
      abbreviations: new Set(parts.abbreviations),
      modifiersLast: lastChars(parts.modifiers, true),
      variable: new Set(parts.variable),
      variableLast: lastChars(parts.variable, true),
      variableMin: Math.min(...lengths),
      variableMax: Math.max(...lengths),
      variableJoined: parts.variable_joined,
      variableJoinedLast: lastChars(parts.variable_joined),
      // The pattern matches only a text that ends with a phrase's last word, in ASCII case.
      nerd: new RegExp(`${sep}*(?:${nerd})$`, 'i'),
      nerdLast: lastChars(phrases.map((phrase) => phrase[phrase.length - 1]), true),
      tails,
      tailsLast: lastChars(tails),
      cased: new Set(parts.cased),
    };
  };

  const spaced = (s) => s.includes('  ') || s[0] === ' ' || s[s.length - 1] === ' ';

  // The name as stripping reads it: NFKC, the tables applied, white space runs one space.
  const normalize = (name, rules, printable) => {
    if (printable && rules.asciiKept && !spaced(name)) return name;
    return nfkc(name).replace(rules.tableRe, rules.swap).split(' ').filter(Boolean).join(' ');
  };

  const rstripSeps = (s, rules) => {
    let j = s.length;
    while (j > 0 && rules.seps.has(s[j - 1])) j -= 1;
    return s.slice(0, j);
  };

  // names.is_style_word restricted to ASCII letters, digits, hyphens and underscores, as a
  // number: 0 for none, 1 for a style word, 2 for an abbreviation, 3 for a token of style
  // parts (camel-case, or joined by hyphens or underscores) with at least one style word.
  const styleCode = (token, rules, postscript, ascii) => {
    if (!ascii && !ASCII.test(token)) return 0;
    const words = postscript ? rules.psStyle : rules.style;
    const lower = token.toLowerCase();
    const flat = lower.includes('-') || lower.includes('_') ? lower.replace(JOINS, '') : lower;
    if (words.has(flat)) return 1;
    if (rules.abbreviations.has(flat)) return 2;
    // One part is the token itself, which isn't a style word.
    if (ONE_PART.test(token) || !TOKEN_CHARS.test(token)) return 0;
    let style = false;
    for (const part of token.match(CAMEL) || []) {
      const word = part.toLowerCase();
      if (words.has(word)) style = true;
      else if (!rules.modifiers.has(word)) return 0;
    }
    return style ? 3 : 0;
  };

  // An ASCII key maps each character on its own: it keeps every character but \t to \r, the
  // space, the hyphen and the underscore, in lower case. On ASCII text a head's key is a
  // prefix of the text's, so the walk works the text's key out once (fill) and finds a
  // head's key among k's by a hash of that prefix, without building a string or hashing
  // one. Every other text goes by its key string.
  const kept = (c) => c !== 0x20 && c !== 0x2d && c !== 0x5f && (c < 0x09 || c > 0x0d);
  const asciiKey = (text) => text.toLowerCase().replace(ASCII_DROP, '');
  // A key's hash, from its length n, its first character and its last three (0 for none):
  // a small integer, a cheap Map key, and quick to work out on the table's every key.
  const mix = (h, c) => Math.imul(h, 0x01000193) ^ c;
  const hash = (n, first, last, second, third) =>
    mix(mix(mix(mix(n, first), last), second), third) & 0x3fffffff;
  const hashOf = (key) => {
    const n = key.length;
    const second = n > 1 ? key.charCodeAt(n - 2) : 0;
    const third = n > 2 ? key.charCodeAt(n - 3) : 0;
    return hash(n, key.charCodeAt(0), key.charCodeAt(n - 1), second, third);
  };

  // A call's scratch space: an ASCII text's key characters.
  const scratch = () => ({ chars: new Uint16Array(64) });
  // Work text's key out into the scratch space; returns its length.
  const fill = (space, text) => {
    if (space.chars.length < text.length) space.chars = new Uint16Array(text.length);
    const chars = space.chars;
    let n = 0;
    for (let i = 0; i < text.length; i += 1) {
      const c = text.charCodeAt(i);
      if (kept(c)) {
        chars[n] = c >= 0x41 && c <= 0x5a ? c | 0x20 : c;
        n += 1;
      }
    }
    return n;
  };
  // Whether key is the first n characters of the filled key.
  const same = (key, chars, n) => {
    if (key.length !== n) return false;
    for (let i = n - 1; i >= 0; i -= 1) if (key.charCodeAt(i) !== chars[i]) return false;
    return true;
  };
  // k's key that is the first n characters of the filled key (n > 0), or null.
  const find = (tbl, space, n) => {
    const chars = space.chars;
    const second = n > 1 ? chars[n - 2] : 0;
    const third = n > 2 ? chars[n - 3] : 0;
    const held = tbl.hashed.get(hash(n, chars[0], chars[n - 1], second, third));
    if (held === undefined) return null;
    if (typeof held === 'string') return same(held, chars, n) ? held : null;
    for (const key of held) if (same(key, chars, n)) return key;
    return null;
  };

  // The ASCII key length of text.slice(0, j), from n, text's own.
  const keyLengthBefore = (text, j, n) => {
    let left = n;
    for (let i = j; i < text.length; i += 1) if (kept(text.charCodeAt(i))) left -= 1;
    return left;
  };

  // A walk's state: its text, whether that is ASCII, and its key: on ASCII text the key's
  // length n (its characters are in the scratch space); otherwise the key itself.
  const keyNow = (state) => (state.ascii ? asciiKey(state.text) : state.key);

  // Make text, a prefix of state.text, the walk's text when its key isn't empty.
  const head = (state, text) => {
    if (state.ascii) {
      const n = keyLengthBefore(state.text, text.length, state.n);
      if (n === 0) return false;
      state.n = n;
    } else {
      const key = matchKey(text);
      if (!key) return false;
      state.key = key;
    }
    state.text = text;
    return true;
  };

  // state.text without one trailing unit, as head sets it; returns what the unit was, or
  // null (state unchanged) when none strips (owned.py's _strip). Every branch keeps a head
  // with a non-empty key, so "- Bold" stops at "bold".
  const strip = (state, rules) => {
    const text = state.text;
    const ascii = state.ascii;
    const length = text.length;
    const last = text.charCodeAt(length - 1); // NaN for '', which no table holds
    const lower = last >= 0x41 && last <= 0x5a ? last | 0x20 : last;
    if (rules.nerdLast[lower] === 1) {
      const m = rules.nerd.exec(text);
      if (m !== null && head(state, text.slice(0, m.index))) return 'patched';
    }
    if (rules.tailsLast[last] === 1) {
      for (const tail of rules.tails) {
        if (text.endsWith(tail)) {
          const rest = text.slice(0, length - tail.length);
          const after = rules.cased.has(tail) ? rules.seps : rules.afterToken;
          if (rest && after.has(rest[rest.length - 1]) && head(state, rstripSeps(rest, rules))) {
            return 'patched';
          }
        }
      }
    }
    // The last separator, in one pass from the end (each a single unit).
    let cut = length - 1;
    while (cut >= 0 && rules.sepCodes[text.charCodeAt(cut)] !== 1) cut -= 1;
    const wordLength = length - cut - 1;
    if (
      cut > 0 &&
      rules.variableLast[lower] === 1 &&
      wordLength >= rules.variableMin &&
      wordLength <= rules.variableMax
    ) {
      const word = text.slice(cut + 1);
      if (
        (ascii || ASCII.test(word)) &&
        rules.variable.has(word.toLowerCase()) &&
        head(state, rstripSeps(text.slice(0, cut), rules))
      ) {
        return 'variable';
      }
    }
    if (rules.variableJoinedLast[last] === 1) {
      for (const tail of rules.variableJoined) {
        const before = length - tail.length - 1;
        const c = before >= 0 ? text.charCodeAt(before) : 0;
        // A lower-case letter ends the head, so its key isn't empty.
        if (c >= 0x61 && c <= 0x7a && text.endsWith(tail)) {
          if (head(state, text.slice(0, before + 1))) return 'variable';
        }
      }
    }
    let end = text.lastIndexOf(' ');
    if (end < 0) {
      // A PostScript name: only the part after the last hyphen can go, so
      // "Inter-Tight-Bold" reaches "Inter-Tight" and never "Inter".
      const dash = text.lastIndexOf('-');
      return dash >= 0 &&
        styleCode(text.slice(dash + 1), rules, true, ascii) !== 0 &&
        head(state, text.slice(0, dash))
        ? 'postscript'
        : null;
    }
    // The last word's style unit goes (owned.py's _style_tail). A style word or abbreviation
    // goes whole ("Semi-Bold", "Med"); in a token of style parts, only the last part goes,
    // with the modifiers before it, so "Archivo BlackItalic" reaches "Archivo Black" before
    // "Archivo", as "Archivo Black Italic" does. A token that ends in a modifier
    // ("BoldExtra") strips nothing.
    const token = text.slice(end + 1);
    const code = styleCode(token, rules, false, ascii);
    if (code === 0) return null;
    if (code === 3) {
      const parts = [...token.matchAll(CAMEL)];
      let n = parts.length - 1;
      if (!rules.style.has(parts[n][0].toLowerCase())) return null;
      while (n > 0 && rules.modifiers.has(parts[n - 1][0].toLowerCase())) n -= 1;
      let stays = parts[n].index;
      while (stays > 0 && (token[stays - 1] === '-' || token[stays - 1] === '_')) stays -= 1;
      // What stays of the token ends in a letter or digit, so its key isn't empty.
      if (stays > 0 && head(state, text.slice(0, end + 1 + stays))) return 'style';
    }
    // When the whole word goes, so do the modifiers before a style word ("Extra Light"), but
    // never the first word; words are found by index where split(' ') and join(' ') would
    // find them.
    while (code !== 2) {
      const before = text.charCodeAt(end - 1);
      if (rules.modifiersLast[before >= 0x41 && before <= 0x5a ? before | 0x20 : before] !== 1) {
        break;
      }
      const start = text.lastIndexOf(' ', end - 1);
      const word = text.slice(start + 1, end);
      if (start < 0 || (!ascii && !ASCII.test(word))) break;
      if (!rules.modifiers.has(word.toLowerCase())) break;
      end = start;
    }
    return head(state, text.slice(0, end)) ? 'style' : null;
  };

  // A name's candidates (owned._chain): its key, then one trailing unit stripped at a time,
  // longest first. With a list, each goes into it as {key, text, patched, postscript} and
  // the result is null; without, the walk stops at the first key that the table's k holds
  // and returns {key, patched, postscript, codes}, or null when none is. Only the first key
  // is the name's own; the others are of the normalized text's heads.
  const chain = (name, tbl, out, space) => {
    const { rules, k } = tbl;
    const printable = PRINTABLE.test(name);
    const ascii = printable || ASCII.test(name);
    let key = null;
    let n = 0;
    if (ascii) {
      n = fill(space, name);
      if (n === 0) return null;
      if (out !== null) key = asciiKey(name);
      else {
        key = find(tbl, space, n);
        if (key !== null) return { key, patched: false, postscript: false, codes: k.get(key) };
      }
    } else {
      key = matchKey(name);
      if (!key) return null;
      const codes = out === null ? k.get(key) : undefined;
      if (codes !== undefined) return { key, patched: false, postscript: false, codes };
    }
    const text = normalize(name, rules, printable);
    const state = { text, ascii: text === name ? ascii : ASCII.test(text), n, key: null };
    if (state.ascii && text !== name) state.n = fill(space, text);
    if (out !== null) out.push({ key, text, patched: false, postscript: false });
    let patched = false;
    let postscript = false;
    let psSteps = 0;
    for (let kind = strip(state, rules); kind !== null; kind = strip(state, rules)) {
      patched = patched || kind === 'patched';
      if (kind === 'postscript') {
        // Only a family part that one hyphen set apart is certain (ArchivoBlack-Regular,
        // Milestone 3 step 5): in Archivo-Black-Bold either hyphen may end the family.
        psSteps += 1;
        postscript = postscript || (psSteps === 1 && !state.text.includes('-'));
      }
      if (out !== null) {
        out.push({ key: keyNow(state), text: state.text, patched, postscript });
      } else if (state.ascii) {
        const now = find(tbl, space, state.n);
        if (now !== null) return { key: now, patched, postscript, codes: k.get(now) };
      } else {
        const codes = k.get(state.key);
        if (codes !== undefined) return { key: state.key, patched, postscript, codes };
      }
    }
    return null;
  };

  const index = (text, size) => {
    if (!INDEX.test(text) || Number(text) >= size) throw refuse(`bad index ${text} (size ${size})`);
    return Number(text);
  };
  const parseCode = (code, ids, reasons) => {
    if (code === '-') return { family: null, letter: '', known: true, reason: null };
    if (code[0] === '!') {
      const reason = reasons[index(code.slice(1), reasons.length)];
      return { family: null, letter: '', known: false, reason };
    }
    const last = code[code.length - 1];
    const letter = last !== undefined && LETTERS.includes(last) ? last : '';
    const family = ids[index(code.slice(0, code.length - letter.length), ids.length)];
    return { family, letter, known: false, reason: null };
  };
  const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
  const strings = (value, name) => {
    if (!Array.isArray(value) || !value.every((item) => typeof item === 'string')) {
      throw refuse(`${name} must be a list of strings`);
    }
    return Object.freeze([...value]);
  };

  // A match file (section 11), parsed, ready to match against, with its rules compiled;
  // refused (an Error with code 'malformed') exactly where Table.from_document refuses it:
  // a wrong format or version, ids or reasons no list of strings, k or amb no object, a bad
  // code or index, an amb key missing from k or no list of integers, or bad words.
  const table = (doc) => {
    if (!isObject(doc) || doc.format !== FORMAT || doc.v !== VERSION) {
      throw refuse(`not a ${FORMAT} file of version ${VERSION}`);
    }
    const ids = strings(doc.ids, 'ids');
    const reasons = strings(doc.reasons, 'reasons');
    if (!isObject(doc.k) || !isObject(doc.amb)) throw refuse('k and amb must be objects');
    const k = new Map();
    for (const key of Object.keys(doc.k)) {
      const value = doc.k[key];
      if (typeof value !== 'string') throw refuse(`bad entry ${key}`);
      k.set(key, value.split(',').map((code) => parseCode(code, ids, reasons)));
    }
    const amb = new Map();
    for (const key of Object.keys(doc.amb)) {
      const value = doc.amb[key];
      if (!k.has(key)) throw refuse(`amb key ${key} is not in k`);
      if (!Array.isArray(value) || !value.every(Number.isInteger)) {
        throw refuse(`amb ${key}: indices must be integers`);
      }
      amb.set(key, value.map((i) => ids[index(String(i), ids.length)]));
    }
    // k's keys by hash, a list where hashes meet: a key that no ASCII text has stays unfound.
    const hashed = new Map();
    for (const key of k.keys()) {
      const h = hashOf(key);
      const held = hashed.get(h);
      if (held === undefined) hashed.set(h, key);
      else hashed.set(h, [...(typeof held === 'string' ? [held] : held), key]);
    }
    return Object.freeze({ ids, k, amb, rules: compile(doc.words), hashed });
  };

  // A name's candidates under a table's rules, all of them (for tests and tools).
  const candidates = (name, tbl) => {
    const out = [];
    chain(name, tbl, out, scratch());
    return out;
  };

  // Set a family's value in an entry's families and values, as Map.set would: a later value
  // replaces an earlier one, so an ambiguous key's "ambiguous" replaces the near reason the
  // family's own code gave there (owned.py's got_near, where ambiguous wins).
  const put = (families, values, fid, value) => {
    const i = families.indexOf(fid);
    if (i < 0) {
      families.push(fid);
      values.push(value);
    } else {
      values[i] = value;
    }
  };

  // Keep [value, n, shown] for fid unless best holds one before it, compared in that order.
  const keep = (best, fid, value, n, shown) => {
    const held = best.get(fid);
    if (
      held === undefined ||
      value < held[0] ||
      (value === held[0] && (n < held[1] || (n === held[1] && shown < held[2])))
    ) {
      best.set(fid, [value, n, shown]);
    }
  };

  // Which catalog families the entries own, and which they only nearly match (owned.match,
  // whose docstring gives the rules): {owned: {id: {as, how}}, near: {id: {as, why}},
  // counts: {entries, owned, near, known, ineligible, unknown, reasons: {reason: n}}}.
  // parsed is OwnedParse.parse's result or a list of its entries.
  const match = (parsed, tbl, { patched = true } = {}) => {
    const entries = Array.isArray(parsed) ? parsed : parsed.entries;
    // A name's first hit. The last name's is kept, since fc-list repeats a family on line
    // after line; a map of every name would cost more than most walks, which end within a
    // few steps (a parsed name is at most MAX_NAME long).
    let lastName = null;
    let lastFound = null;
    const space = scratch();
    const lookup = (name) => {
      if (name !== lastName) {
        lastFound = chain(name, tbl, null, space);
        lastName = name;
      }
      return lastFound;
    };
    const owned = new Map(); // id -> [index in HOW, entry, shown]
    const near = new Map(); // id -> [index in WHY, entry, shown]
    const counts = { owned: 0, near: 0, known: 0, ineligible: 0, unknown: 0 };
    const reasons = new Map();
    // One entry's families and what each got (an index in HOW or WHY), and its reasons.
    const ownIds = [];
    const ownHow = [];
    const nearIds = [];
    const nearWhy = [];
    const why = [];
    for (let n = 0; n < entries.length; n += 1) {
      // The names in matching order (owned._tries): the families, then the full name and
      // the PostScript name. Only a trusted entry's first family is trusted; a PostScript
      // name's family part before its one hyphen is, through the hit (Milestone 3 step 5).
      const entry = entries[n];
      const families = entry.families;
      let found = null;
      let shown = null;
      let trusted = false;
      for (let i = 0; found === null && i < families.length; i += 1) {
        found = lookup(families[i]);
        shown = families[i];
        trusted = entry.trusted && i === 0;
      }
      if (found === null && entry.full) {
        found = lookup(entry.full);
        shown = entry.full;
        trusted = false;
      }
      if (found === null && entry.postscript) {
        found = lookup(entry.postscript);
        shown = entry.postscript;
        trusted = false;
      }
      if (found === null) {
        counts.unknown += 1;
        continue;
      }
      trusted = trusted || found.postscript;
      const amb = tbl.amb.get(found.key);
      const ambiguous = amb !== undefined && amb.length > 0;
      const doubt = ambiguous && !trusted;
      ownIds.length = 0;
      ownHow.length = 0;
      nearIds.length = 0;
      nearWhy.length = 0;
      why.length = 0;
      let known = false;
      let hasFamily = false;
      const codes = found.codes;
      for (let c = 0; c < codes.length; c += 1) {
        const code = codes[c];
        if (code.family === null) {
          known = known || code.known;
          if (code.reason !== null && !why.includes(code.reason)) why.push(code.reason);
          continue;
        }
        hasFamily = true;
        const letter = found.patched && SAME_FONT.has(code.letter) ? 'n' : code.letter;
        if (letter === 'n' && !patched) put(nearIds, nearWhy, code.family, PATCHED);
        else if (!HOW_INDEX.has(letter)) {
          put(nearIds, nearWhy, code.family, WHY_INDEX.get(letter));
        }
        else if (doubt) put(nearIds, nearWhy, code.family, AMBIGUOUS);
        else put(ownIds, ownHow, code.family, HOW_INDEX.get(letter));
      }
      if (ambiguous && (doubt || (known && !hasFamily))) {
        for (const fid of amb) if (!ownIds.includes(fid)) put(nearIds, nearWhy, fid, AMBIGUOUS);
      }
      for (let i = 0; i < ownIds.length; i += 1) keep(owned, ownIds[i], ownHow[i], n, shown);
      for (let i = 0; i < nearIds.length; i += 1) keep(near, nearIds[i], nearWhy[i], n, shown);
      if (ownIds.length) counts.owned += 1;
      else if (nearIds.length) counts.near += 1;
      else if (known) counts.known += 1;
      else if (why.length) {
        counts.ineligible += 1;
        for (const reason of why) reasons.set(reason, (reasons.get(reason) || 0) + 1);
      } else counts.unknown += 1;
    }
    // The result's keys are the file's ids and reasons, so its objects are built from entries:
    // assigning to a "__proto__" key would set the object's prototype, not add the key.
    const sorted = (map, value) =>
      Object.fromEntries([...map.keys()].sort().map((key) => [key, value(map.get(key))]));
    for (const fid of owned.keys()) near.delete(fid);
    return {
      owned: sorted(owned, ([how, , as]) => ({ as, how: HOW[how] })),
      near: sorted(near, ([reason, , as]) => ({ as, why: WHY[reason] })),
      counts: { entries: entries.length, ...counts, reasons: sorted(reasons, (count) => count) },
    };
  };

  return Object.freeze({ table, match, candidates, FORMAT, VERSION, HOW, WHY, OUTCOMES });
})();
