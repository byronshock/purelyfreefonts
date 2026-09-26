// 05-keys: the name keys used by search, and by Milestone 3 to match installed fonts
// (site/CONTRACT.md, "JS parts"). A port of src/tff_catalog/keys.py. The shared vector file
// tests/vectors/name-keys.json is the source of truth: tests/site/test_keys_js.py runs every
// case in Chromium and Firefox and checks that both constants below equal its spec.
//
// matchKey(s):  NFKC, casefold, NFKC, drop DROP_CODEPOINTS, NFC. Accents are kept.
// searchKey(s): matchKey(s), NFD, drop general category Mn, NFC. Accents are stripped.
//
// JavaScript has no casefold. Python's str.casefold() gives the same as toLowerCase(), with
// sharp s to "ss" and final sigma to medial sigma, for every code point that can follow NFKC
// except those in CASEFOLD_EXTRA. Each code point is folded on its own, as the spec says, so
// toLowerCase()'s final-sigma rule never applies. Anything that isn't a string is read as
// String(s), and null or undefined as ''. A lone surrogate is kept, as Python keeps it.
const Keys = (() => {
  // Whitespace, hyphens and dashes, underscores and invisible joiners: hex code points and
  // inclusive ranges, in the vector file's order.
  const DROP_CODEPOINTS = Object.freeze([
    '0009-000D', '0020', '0085', '00A0', '1680', '2000-200A', '2028', '2029', '202F', '205F',
    '3000', '002D', '00AD', '2010-2014', '2212', 'FE63', 'FF0D', '005F', '200B-200D', '2060',
    'FEFF',
  ]);

  // Code point to its casefold, in hex, where casefold differs from the recipe above: Cherokee,
  // Greek iota subscripts, old Cyrillic letter variants and a few letters with marks.
  const CASEFOLD_EXTRA = Object.freeze({
    '01F0': '006A 030C', '0345': '03B9', '0390': '03B9 0308 0301', '03B0': '03C5 0308 0301',
    '13A0': '13A0', '13A1': '13A1', '13A2': '13A2', '13A3': '13A3', '13A4': '13A4', '13A5': '13A5',
    '13A6': '13A6', '13A7': '13A7', '13A8': '13A8', '13A9': '13A9', '13AA': '13AA', '13AB': '13AB',
    '13AC': '13AC', '13AD': '13AD', '13AE': '13AE', '13AF': '13AF', '13B0': '13B0', '13B1': '13B1',
    '13B2': '13B2', '13B3': '13B3', '13B4': '13B4', '13B5': '13B5', '13B6': '13B6', '13B7': '13B7',
    '13B8': '13B8', '13B9': '13B9', '13BA': '13BA', '13BB': '13BB', '13BC': '13BC', '13BD': '13BD',
    '13BE': '13BE', '13BF': '13BF', '13C0': '13C0', '13C1': '13C1', '13C2': '13C2', '13C3': '13C3',
    '13C4': '13C4', '13C5': '13C5', '13C6': '13C6', '13C7': '13C7', '13C8': '13C8', '13C9': '13C9',
    '13CA': '13CA', '13CB': '13CB', '13CC': '13CC', '13CD': '13CD', '13CE': '13CE', '13CF': '13CF',
    '13D0': '13D0', '13D1': '13D1', '13D2': '13D2', '13D3': '13D3', '13D4': '13D4', '13D5': '13D5',
    '13D6': '13D6', '13D7': '13D7', '13D8': '13D8', '13D9': '13D9', '13DA': '13DA', '13DB': '13DB',
    '13DC': '13DC', '13DD': '13DD', '13DE': '13DE', '13DF': '13DF', '13E0': '13E0', '13E1': '13E1',
    '13E2': '13E2', '13E3': '13E3', '13E4': '13E4', '13E5': '13E5', '13E6': '13E6', '13E7': '13E7',
    '13E8': '13E8', '13E9': '13E9', '13EA': '13EA', '13EB': '13EB', '13EC': '13EC', '13ED': '13ED',
    '13EE': '13EE', '13EF': '13EF', '13F0': '13F0', '13F1': '13F1', '13F2': '13F2', '13F3': '13F3',
    '13F4': '13F4', '13F5': '13F5', '13F8': '13F0', '13F9': '13F1', '13FA': '13F2', '13FB': '13F3',
    '13FC': '13F4', '13FD': '13F5', '1C80': '0432', '1C81': '0434', '1C82': '043E', '1C83': '0441',
    '1C84': '0442', '1C85': '0442', '1C86': '044A', '1C87': '0463', '1C88': 'A64B',
    '1E96': '0068 0331', '1E97': '0074 0308', '1E98': '0077 030A', '1E99': '0079 030A',
    '1F50': '03C5 0313', '1F52': '03C5 0313 0300', '1F54': '03C5 0313 0301',
    '1F56': '03C5 0313 0342', '1F80': '1F00 03B9', '1F81': '1F01 03B9', '1F82': '1F02 03B9',
    '1F83': '1F03 03B9', '1F84': '1F04 03B9', '1F85': '1F05 03B9', '1F86': '1F06 03B9',
    '1F87': '1F07 03B9', '1F88': '1F00 03B9', '1F89': '1F01 03B9', '1F8A': '1F02 03B9',
    '1F8B': '1F03 03B9', '1F8C': '1F04 03B9', '1F8D': '1F05 03B9', '1F8E': '1F06 03B9',
    '1F8F': '1F07 03B9', '1F90': '1F20 03B9', '1F91': '1F21 03B9', '1F92': '1F22 03B9',
    '1F93': '1F23 03B9', '1F94': '1F24 03B9', '1F95': '1F25 03B9', '1F96': '1F26 03B9',
    '1F97': '1F27 03B9', '1F98': '1F20 03B9', '1F99': '1F21 03B9', '1F9A': '1F22 03B9',
    '1F9B': '1F23 03B9', '1F9C': '1F24 03B9', '1F9D': '1F25 03B9', '1F9E': '1F26 03B9',
    '1F9F': '1F27 03B9', '1FA0': '1F60 03B9', '1FA1': '1F61 03B9', '1FA2': '1F62 03B9',
    '1FA3': '1F63 03B9', '1FA4': '1F64 03B9', '1FA5': '1F65 03B9', '1FA6': '1F66 03B9',
    '1FA7': '1F67 03B9', '1FA8': '1F60 03B9', '1FA9': '1F61 03B9', '1FAA': '1F62 03B9',
    '1FAB': '1F63 03B9', '1FAC': '1F64 03B9', '1FAD': '1F65 03B9', '1FAE': '1F66 03B9',
    '1FAF': '1F67 03B9', '1FB2': '1F70 03B9', '1FB3': '03B1 03B9', '1FB4': '03AC 03B9',
    '1FB6': '03B1 0342', '1FB7': '03B1 0342 03B9', '1FBC': '03B1 03B9', '1FC2': '1F74 03B9',
    '1FC3': '03B7 03B9', '1FC4': '03AE 03B9', '1FC6': '03B7 0342', '1FC7': '03B7 0342 03B9',
    '1FCC': '03B7 03B9', '1FD2': '03B9 0308 0300', '1FD6': '03B9 0342', '1FD7': '03B9 0308 0342',
    '1FE2': '03C5 0308 0300', '1FE4': '03C1 0313', '1FE6': '03C5 0342', '1FE7': '03C5 0308 0342',
    '1FF2': '1F7C 03B9', '1FF3': '03C9 03B9', '1FF4': '03CE 03B9', '1FF6': '03C9 0342',
    '1FF7': '03C9 0342 03B9', '1FFC': '03C9 03B9', 'AB70': '13A0', 'AB71': '13A1', 'AB72': '13A2',
    'AB73': '13A3', 'AB74': '13A4', 'AB75': '13A5', 'AB76': '13A6', 'AB77': '13A7', 'AB78': '13A8',
    'AB79': '13A9', 'AB7A': '13AA', 'AB7B': '13AB', 'AB7C': '13AC', 'AB7D': '13AD', 'AB7E': '13AE',
    'AB7F': '13AF', 'AB80': '13B0', 'AB81': '13B1', 'AB82': '13B2', 'AB83': '13B3', 'AB84': '13B4',
    'AB85': '13B5', 'AB86': '13B6', 'AB87': '13B7', 'AB88': '13B8', 'AB89': '13B9', 'AB8A': '13BA',
    'AB8B': '13BB', 'AB8C': '13BC', 'AB8D': '13BD', 'AB8E': '13BE', 'AB8F': '13BF', 'AB90': '13C0',
    'AB91': '13C1', 'AB92': '13C2', 'AB93': '13C3', 'AB94': '13C4', 'AB95': '13C5', 'AB96': '13C6',
    'AB97': '13C7', 'AB98': '13C8', 'AB99': '13C9', 'AB9A': '13CA', 'AB9B': '13CB', 'AB9C': '13CC',
    'AB9D': '13CD', 'AB9E': '13CE', 'AB9F': '13CF', 'ABA0': '13D0', 'ABA1': '13D1', 'ABA2': '13D2',
    'ABA3': '13D3', 'ABA4': '13D4', 'ABA5': '13D5', 'ABA6': '13D6', 'ABA7': '13D7', 'ABA8': '13D8',
    'ABA9': '13D9', 'ABAA': '13DA', 'ABAB': '13DB', 'ABAC': '13DC', 'ABAD': '13DD', 'ABAE': '13DE',
    'ABAF': '13DF', 'ABB0': '13E0', 'ABB1': '13E1', 'ABB2': '13E2', 'ABB3': '13E3', 'ABB4': '13E4',
    'ABB5': '13E5', 'ABB6': '13E6', 'ABB7': '13E7', 'ABB8': '13E8', 'ABB9': '13E9', 'ABBA': '13EA',
    'ABBB': '13EB', 'ABBC': '13EC', 'ABBD': '13ED', 'ABBE': '13EE', 'ABBF': '13EF',
  });

  const SHARP_S = '\u00DF';
  const FINAL_SIGMA = '\u03C2';
  const SIGMA = '\u03C3';

  const fromHex = (hex) => String.fromCodePoint(parseInt(hex, 16));

  // Character (not hex) to its casefold string.
  const EXTRA = new Map(
    Object.entries(CASEFOLD_EXTRA).map(([from, to]) => [
      fromHex(from),
      to.split(' ').map(fromHex).join(''),
    ]),
  );

  // One character class for every dropped code point: [\u{0009}-\u{000D}\u{0020}...].
  const classItem = (entry) =>
    entry
      .split('-')
      .map((hex) => `\\u{${hex}}`)
      .join('-');
  const DROP = new RegExp(`[${DROP_CODEPOINTS.map(classItem).join('')}]`, 'gu');
  const MN = /\p{Mn}/gu;

  const asString = (s) => (typeof s === 'string' ? s : String(s ?? ''));

  // s.normalize(form), except that a lone surrogate (from a paste or a broken font name) stays
  // as it is, as in Python and Chromium; Firefox would turn it into U+FFFD. A lone surrogate
  // never combines with its neighbours, so the text between them is normalized piece by piece.
  const LONE = /(\p{Cs})/u;
  const normalize = (s, form) =>
    LONE.test(s)
      ? s
          .split(LONE)
          .map((piece, i) => (i % 2 ? piece : piece.normalize(form)))
          .join('')
      : s.normalize(form);

  // Python's str.casefold(), for text that is already NFKC.
  const casefold = (s) => {
    let out = '';
    for (const ch of s) {
      const extra = EXTRA.get(ch);
      out +=
        extra ?? ch.toLowerCase().replaceAll(SHARP_S, 'ss').replaceAll(FINAL_SIGMA, SIGMA);
    }
    return out;
  };

  // The exact-match key: case, width and spacing folded, accents kept.
  const matchKey = (s) => {
    const folded = casefold(normalize(asString(s), 'NFKC'));
    // Dropping a joiner or space can leave a letter next to a combining mark; NFC recomposes
    // them, so the key is idempotent.
    return normalize(normalize(folded, 'NFKC').replace(DROP, ''), 'NFC');
  };

  // The search key: matchKey with accents stripped. Letters with no decomposition, such as
  // l with stroke, stay as they are.
  const searchKey = (s) => normalize(normalize(matchKey(s), 'NFD').replace(MN, ''), 'NFC');

  return Object.freeze({ matchKey, searchKey, DROP_CODEPOINTS, CASEFOLD_EXTRA });
})();
