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
const OwnedMatch = (() => {
  const FORMAT = 'tff-match';
  const VERSION = 1;
  const HOW = Object.freeze(['family', 'postscript', 'build', 'rename', 'patched']);
  const WHY = Object.freeze(['ambiguous', 'patched', 'sibling', 'related', 'bundle']);
  const OUTCOMES = Object.freeze(['owned', 'near', 'known', 'ineligible', 'unknown']);
  const HOW_OF = new Map([
    ['', 'family'], ['p', 'postscript'], ['b', 'build'], ['r', 'rename'], ['n', 'patched'],
  ]);
  const WHY_OF = new Map([['n', 'patched'], ['s', 'sibling'], ['x', 'related'], ['u', 'bundle']]);
  const SAME_FONT = new Set(['', 'p', 'b', 'r']); // codes that name the font itself
  const LETTERS = 'pbrnusx';
  const LOWER = new Set('abcdefghijklmnopqrstuvwxyz');
  const LOWER_DIGIT = new Set('abcdefghijklmnopqrstuvwxyz0123456789');
  const ASCII = /^[\x00-\x7f]*$/;
  const PRINTABLE = /^[\x20-\x7e]+$/;
  const ASCII_DROP = /[\t-\r \-_]/g;
  const HEX = /^[0-9A-Fa-f]{1,6}$/;
  const INDEX = /^(?:0|[1-9][0-9]*)$/;
  const CAMEL = /[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+/g;
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
    // The pattern matches only a text that ends with a phrase's last word, in ASCII case.
    const nerdTails = [...new Set(phrases.map((phrase) => phrase[phrase.length - 1].toLowerCase()))];
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
      afterToken: new Set([...LOWER_DIGIT, ...seps]),
      style,
      psStyle: new Set([...style, ...parts.postscript]),
      modifiers: new Set(parts.modifiers),
      abbreviations: new Set(parts.abbreviations),
      variable: new Set(parts.variable),
      variableJoined: parts.variable_joined,
      nerd: new RegExp(`${sep}*(?:${nerd})$`, 'i'),
      nerdTails,
      tails: [...byLength(parts.nerd_tokens), ...parts.cased],
      cased: new Set(parts.cased),
    };
  };

  // The name as stripping reads it: NFKC, the tables applied, white space runs one space.
  const normalize = (name, rules) => {
    const spaced = (s) => s.includes('  ') || s[0] === ' ' || s[s.length - 1] === ' ';
    if (rules.asciiKept && PRINTABLE.test(name) && !spaced(name)) return name;
    return nfkc(name).replace(rules.tableRe, rules.swap).split(' ').filter(Boolean).join(' ');
  };

  const rstripSeps = (s, rules) => {
    let j = s.length;
    while (j > 0 && rules.seps.has(s[j - 1])) j -= 1;
    return s.slice(0, j);
  };

  // 'style' or 'abbreviation' when the token is a style word, else null: names.is_style_word
  // restricted to ASCII letters, digits, hyphens and underscores.
  const styleToken = (token, rules, postscript) => {
    if (!ASCII.test(token)) return null;
    const words = postscript ? rules.psStyle : rules.style;
    const flat = token.toLowerCase().replace(/[-_]/g, '');
    if (words.has(flat)) return 'style';
    if (rules.abbreviations.has(flat)) return 'abbreviation';
    if (!TOKEN_CHARS.test(token)) return null;
    const parts = (token.match(CAMEL) || []).map((part) => part.toLowerCase());
    const known = parts.every((part) => words.has(part) || rules.modifiers.has(part));
    return parts.length && known && parts.some((part) => words.has(part)) ? 'style' : null;
  };

  // The style unit that ends a spaced name's last token, as [kind, what stays of the token],
  // or null (owned.py's _style_tail). A style word or abbreviation goes whole ("Semi-Bold",
  // "Med"); in a token of camel-case or hyphen- or underscore-joined parts, only the last part
  // goes, with the modifiers before it, so "Archivo BlackItalic" reaches "Archivo Black" before
  // "Archivo", as "Archivo Black Italic" does. A token that ends in a modifier ("BoldExtra")
  // strips nothing.
  const styleTail = (token, rules) => {
    const kind = styleToken(token, rules, false);
    if (kind === null) return null;
    if (kind === 'abbreviation' || rules.style.has(token.toLowerCase().replace(/[-_]/g, ''))) {
      return [kind, ''];
    }
    const parts = [...token.matchAll(CAMEL)];
    let n = parts.length - 1;
    if (!rules.style.has(parts[n][0].toLowerCase())) return null;
    while (n > 0 && rules.modifiers.has(parts[n - 1][0].toLowerCase())) n -= 1;
    let end = parts[n].index;
    while (end > 0 && (token[end - 1] === '-' || token[end - 1] === '_')) end -= 1;
    return [kind, token.slice(0, end)];
  };

  // [head, key] when the head's key is non-empty, else null.
  const keyed = (head) => {
    const key = matchKey(head);
    return key ? [head, key] : null;
  };

  // The text without one trailing unit, what the unit was and the new text's key, as
  // [text, kind, key]; null when none strips. Every branch keeps a head with a non-empty key,
  // so "- Bold" stops at "bold".
  const stripUnit = (text, rules) => {
    const ends = (tail) => text.length >= tail.length && text.slice(-tail.length).toLowerCase() === tail;
    const m = rules.nerdTails.some(ends) ? rules.nerd.exec(text) : null;
    const nerd = m === null ? null : keyed(text.slice(0, m.index));
    if (nerd !== null) return [nerd[0], 'patched', nerd[1]];
    for (const tail of rules.tails) {
      if (text.endsWith(tail)) {
        const head = text.slice(0, text.length - tail.length);
        const after = rules.cased.has(tail) ? rules.seps : rules.afterToken;
        if (head && after.has(head[head.length - 1])) {
          const kept = keyed(rstripSeps(head, rules));
          if (kept !== null) return [kept[0], 'patched', kept[1]];
        }
      }
    }
    // The last separator, in one pass from the end (each a single unit).
    let cut = text.length - 1;
    while (cut >= 0 && !rules.seps.has(text[cut])) cut -= 1;
    if (cut > 0) {
      const word = text.slice(cut + 1);
      if (ASCII.test(word) && rules.variable.has(word.toLowerCase())) {
        const head = keyed(rstripSeps(text.slice(0, cut), rules));
        if (head !== null) return [head[0], 'variable', head[1]];
      }
    }
    for (const tail of rules.variableJoined) {
      const before = text.length - tail.length - 1;
      if (text.endsWith(tail) && before >= 0 && LOWER.has(text[before])) {
        const head = text.slice(0, before + 1);
        return [head, 'variable', matchKey(head)];
      }
    }
    if (!text.includes(' ')) {
      // A PostScript name: only the part after the last hyphen can go, so
      // "Inter-Tight-Bold" reaches "Inter-Tight" and never "Inter".
      const dash = text.lastIndexOf('-');
      const family = dash >= 0 ? keyed(text.slice(0, dash)) : null;
      if (family !== null && styleToken(text.slice(dash + 1), rules, true)) {
        return [family[0], 'postscript', family[1]];
      }
      return null;
    }
    // The last word's style unit goes (styleTail). When the whole word goes, so do the
    // modifiers before a style word ("Extra Light"), but never the first word; words are found
    // by index where split(' ') and join(' ') would find them.
    let end = text.lastIndexOf(' ');
    const tail = styleTail(text.slice(end + 1), rules);
    if (tail === null) return null;
    const [kind, rest] = tail;
    if (rest) {
      const kept = text.slice(0, end + 1) + rest;
      return [kept, 'style', matchKey(kept)];
    }
    while (kind === 'style') {
      const start = text.lastIndexOf(' ', end - 1);
      const word = text.slice(start + 1, end);
      if (start < 0 || !ASCII.test(word) || !rules.modifiers.has(word.toLowerCase())) break;
      end = start;
    }
    const head = keyed(text.slice(0, end));
    return head === null ? null : [head[0], 'style', head[1]];
  };

  // A name's candidates (owned.candidates): its key, then one trailing unit stripped at a
  // time, longest first, each as {key, text, patched, postscript}. Each goes to visit; the
  // first value visit returns other than undefined ends the walk, and walk returns it.
  const walk = (name, rules, visit) => {
    const key = matchKey(name);
    if (!key) return undefined;
    let text = normalize(name, rules);
    let patched = false;
    let postscript = false;
    let psSteps = 0;
    let found = visit({ key, text, patched, postscript });
    while (found === undefined) {
      const step = stripUnit(text, rules);
      if (step === null) break;
      text = step[0];
      patched = patched || step[1] === 'patched';
      if (step[1] === 'postscript') {
        // Only a family part that one hyphen set apart is certain (ArchivoBlack-Regular,
        // Milestone 3 step 5): in Archivo-Black-Bold either hyphen may end the family.
        psSteps += 1;
        postscript = postscript || (psSteps === 1 && !text.includes('-'));
      }
      found = visit({ key: step[2], text, patched, postscript });
    }
    return found;
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
    return Object.freeze({ ids, k, amb, rules: compile(doc.words) });
  };

  // A name's candidates under a table's rules, all of them (for tests and tools).
  const candidates = (name, tbl) => {
    const out = [];
    walk(name, tbl.rules, (cand) => {
      out.push(cand);
    });
    return out;
  };

  // The first of a name's candidates whose key the table knows, with its codes, or null; the
  // chain is worked out only that far.
  const firstHit = (name, tbl) => {
    const found = walk(name, tbl.rules, (cand) => {
      const codes = tbl.k.get(cand.key);
      return codes === undefined ? undefined : [cand, codes];
    });
    return found === undefined ? null : found;
  };

  // [name, trusted] in matching order. A PostScript name is no more trusted than any other:
  // only its family part before its one hyphen is, through the candidate's postscript, so
  // "ArchivoBlack-Regular" can own Archivo Black and "Archivo-Black" can't.
  const tries = (entry) => {
    const out = entry.families.map((family, n) => [family, entry.trusted && n === 0]);
    if (entry.full) out.push([entry.full, false]);
    if (entry.postscript) out.push([entry.postscript, false]);
    return out;
  };

  // An entry's first hit: [name, candidate, codes, trusted], or null. seen keeps each name's
  // firstHit for one match call, so a family that many entries name is worked out once.
  const hit = (entry, tbl, seen) => {
    for (const [name, trusted] of tries(entry)) {
      let found = seen.get(name);
      if (found === undefined) {
        found = firstHit(name, tbl);
        seen.set(name, found);
      }
      if (found !== null) return [name, found[0], found[1], trusted || found[0].postscript];
    }
    return null;
  };

  const before = (a, b) => a[0] - b[0] || a[1] - b[1] || (a[2] < b[2] ? -1 : a[2] > b[2] ? 1 : 0);
  const keep = (best, fid, rank) => {
    if (!best.has(fid) || before(rank, best.get(fid)) < 0) best.set(fid, rank);
  };

  // Which catalog families the entries own, and which they only nearly match (owned.match,
  // whose docstring gives the rules): {owned: {id: {as, how}}, near: {id: {as, why}},
  // counts: {entries, owned, near, known, ineligible, unknown, reasons: {reason: n}}}.
  // parsed is OwnedParse.parse's result or a list of its entries.
  const match = (parsed, tbl, { patched = true } = {}) => {
    const entries = Array.isArray(parsed) ? parsed : parsed.entries;
    const seen = new Map();
    const owned = new Map();
    const near = new Map();
    const counts = { owned: 0, near: 0, known: 0, ineligible: 0, unknown: 0 };
    const reasons = new Map();
    entries.forEach((entry, n) => {
      const found = hit(entry, tbl, seen);
      if (found === null) {
        counts.unknown += 1;
        return;
      }
      const [shown, cand, codes, trusted] = found;
      const amb = tbl.amb.get(cand.key) || [];
      const doubt = amb.length > 0 && !trusted;
      const gotOwned = new Map();
      const gotNear = new Map();
      const why = new Set();
      let known = false;
      let hasFamily = false;
      for (const code of codes) {
        if (code.family === null) {
          known = known || code.known;
          if (code.reason !== null) why.add(code.reason);
          continue;
        }
        hasFamily = true;
        const letter = cand.patched && SAME_FONT.has(code.letter) ? 'n' : code.letter;
        if (letter === 'n' && !patched) gotNear.set(code.family, 'patched');
        else if (!HOW_OF.has(letter)) gotNear.set(code.family, WHY_OF.get(letter));
        else if (doubt) gotNear.set(code.family, 'ambiguous');
        else gotOwned.set(code.family, HOW_OF.get(letter));
      }
      if (amb.length && (doubt || (known && !hasFamily))) {
        for (const fid of amb) if (!gotOwned.has(fid)) gotNear.set(fid, 'ambiguous');
      }
      for (const [fid, how] of gotOwned) keep(owned, fid, [HOW.indexOf(how), n, shown]);
      for (const [fid, reason] of gotNear) keep(near, fid, [WHY.indexOf(reason), n, shown]);
      if (gotOwned.size) counts.owned += 1;
      else if (gotNear.size) counts.near += 1;
      else if (known) counts.known += 1;
      else if (why.size) {
        counts.ineligible += 1;
        for (const reason of why) reasons.set(reason, (reasons.get(reason) || 0) + 1);
      } else counts.unknown += 1;
    });
    const result = { owned: {}, near: {}, counts: { entries: entries.length, ...counts } };
    for (const fid of [...owned.keys()].sort()) {
      result.owned[fid] = { as: owned.get(fid)[2], how: HOW[owned.get(fid)[0]] };
    }
    for (const fid of [...near.keys()].sort()) {
      if (!owned.has(fid)) result.near[fid] = { as: near.get(fid)[2], why: WHY[near.get(fid)[0]] };
    }
    result.counts.reasons = {};
    for (const reason of [...reasons.keys()].sort()) {
      result.counts.reasons[reason] = reasons.get(reason);
    }
    return result;
  };

  return Object.freeze({ table, match, candidates, FORMAT, VERSION, HOW, WHY, OUTCOMES });
})();
