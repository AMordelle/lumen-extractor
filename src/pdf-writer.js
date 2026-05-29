const PAGE_WIDTH = 612;
const PAGE_HEIGHT = 792;
const MARGIN_X = 54;
const MARGIN_TOP = 54;
const MARGIN_BOTTOM = 54;
const BODY_SIZE = 11;
const BODY_LEADING = 15;
const SMALL_SIZE = 10;
const SMALL_LEADING = 14;
const CHAPTER_SIZE = 14;
const CHAPTER_LEADING = 19;
const BOOK_SIZE = 19;
const BOOK_LEADING = 25;
const WIN_ANSI = new Map([
  [0x20ac, 0x80], [0x201a, 0x82], [0x0192, 0x83], [0x201e, 0x84], [0x2026, 0x85],
  [0x2020, 0x86], [0x2021, 0x87], [0x02c6, 0x88], [0x2030, 0x89], [0x0160, 0x8a],
  [0x2039, 0x8b], [0x0152, 0x8c], [0x017d, 0x8e], [0x2018, 0x91], [0x2019, 0x92],
  [0x201c, 0x93], [0x201d, 0x94], [0x2022, 0x95], [0x2013, 0x96], [0x2014, 0x97],
  [0x02dc, 0x98], [0x2122, 0x99], [0x0161, 0x9a], [0x203a, 0x9b], [0x0153, 0x9c],
  [0x017e, 0x9e], [0x0178, 0x9f],
]);

function charToWinAnsiByte(char) {
  const code = char.codePointAt(0);
  if ((code >= 0x20 && code <= 0x7e) || (code >= 0xa0 && code <= 0xff)) return code;
  if (code === 0x0a || code === 0x0d || code === 0x09) return 0x20;
  return WIN_ANSI.get(code) ?? null;
}

function encodeWinAnsiHex(text) {
  const bytes = [];
  for (const char of String(text ?? "")) {
    const byte = charToWinAnsiByte(char);
    if (byte === null) {
      throw new Error(`Carácter no compatible con WinAnsi para PDF: U+${char.codePointAt(0).toString(16).toUpperCase()} (${char})`);
    }
    bytes.push(byte);
  }
  return Buffer.from(bytes).toString("hex").toUpperCase();
}

function estimateWidth(text, fontSize) {
  let width = 0;
  for (const char of String(text ?? "")) {
    if (char === " ") width += fontSize * 0.28;
    else if (/[.,;:!¡?¿'`´]/u.test(char)) width += fontSize * 0.25;
    else if (/[ilI|]/u.test(char)) width += fontSize * 0.24;
    else if (/[mwMWÁÉÍÓÚÜÑ]/u.test(char)) width += fontSize * 0.78;
    else if (/[0-9]/u.test(char)) width += fontSize * 0.52;
    else width += fontSize * 0.5;
  }
  return width;
}

function splitLongToken(token, maxWidth, fontSize) {
  const parts = [];
  let current = "";
  for (const char of token) {
    if (current && estimateWidth(`${current}${char}`, fontSize) > maxWidth) {
      parts.push(current);
      current = char;
    } else {
      current += char;
    }
  }
  if (current) parts.push(current);
  return parts;
}

function wrapText(text, maxWidth, fontSize, firstPrefix = "", nextPrefix = "") {
  const paragraphs = String(text ?? "").split(/\r?\n/);
  const lines = [];
  for (const paragraph of paragraphs) {
    const tokens = paragraph.match(/\S+\s*/gu) ?? [""];
    let prefix = firstPrefix;
    let current = prefix;
    for (const token of tokens) {
      const candidate = `${current}${token}`;
      if (current !== prefix && estimateWidth(candidate.trimEnd(), fontSize) > maxWidth) {
        lines.push(current.trimEnd());
        prefix = nextPrefix;
        current = prefix;
      }
      if (estimateWidth(`${current}${token}`.trimEnd(), fontSize) > maxWidth && token.trim().length > 0) {
        for (const part of splitLongToken(token, maxWidth - estimateWidth(current, fontSize), fontSize)) {
          if (current !== prefix) lines.push(current.trimEnd());
          current = `${prefix}${part}`;
          if (estimateWidth(current.trimEnd(), fontSize) >= maxWidth) {
            lines.push(current.trimEnd());
            current = prefix;
          }
        }
      } else {
        current += token;
      }
    }
    lines.push(current.trimEnd());
  }
  return lines;
}

export class PdfWriter {
  constructor() {
    this.pages = [];
    this.current = null;
    this.y = PAGE_HEIGHT - MARGIN_TOP;
    this.addPage();
  }

  addPage() {
    this.current = [];
    this.pages.push(this.current);
    this.y = PAGE_HEIGHT - MARGIN_TOP;
  }

  ensureSpace(height) {
    if (this.y - height < MARGIN_BOTTOM) this.addPage();
  }

  moveDown(amount) {
    this.ensureSpace(amount);
    this.y -= amount;
  }

  drawLine(text, { x = MARGIN_X, font = "F1", size = BODY_SIZE, leading = BODY_LEADING } = {}) {
    this.ensureSpace(leading);
    const hex = encodeWinAnsiHex(text);
    this.current.push(`BT /${font} ${size} Tf 1 0 0 1 ${x.toFixed(2)} ${this.y.toFixed(2)} Tm <${hex}> Tj ET`);
    this.y -= leading;
  }

  drawWrapped(text, { x = MARGIN_X, indent = 0, firstPrefix = "", nextPrefix = "", font = "F1", size = BODY_SIZE, leading = BODY_LEADING, spacingAfter = 0 } = {}) {
    const maxWidth = PAGE_WIDTH - MARGIN_X - x;
    const lines = wrapText(text, maxWidth, size, firstPrefix, nextPrefix);
    lines.forEach((line, index) => {
      this.drawLine(line, { x: index === 0 ? x : x + indent, font, size, leading });
    });
    if (spacingAfter) this.moveDown(spacingAfter);
  }

  toBuffer() {
    const objects = [];
    const add = (body) => {
      objects.push(body);
      return objects.length;
    };

    const catalogId = add("<< /Type /Catalog /Pages 2 0 R >>");
    const pagesId = add("");
    const fontRegularId = add("<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman /Encoding /WinAnsiEncoding >>");
    const fontBoldId = add("<< /Type /Font /Subtype /Type1 /BaseFont /Times-Bold /Encoding /WinAnsiEncoding >>");
    const pageIds = [];

    for (const commands of this.pages) {
      const stream = `${commands.join("\n")}\n`;
      const contentId = add(`<< /Length ${Buffer.byteLength(stream, "ascii")} >>\nstream\n${stream}endstream`);
      const pageId = add(`<< /Type /Page /Parent ${pagesId} 0 R /MediaBox [0 0 ${PAGE_WIDTH} ${PAGE_HEIGHT}] /Resources << /Font << /F1 ${fontRegularId} 0 R /F2 ${fontBoldId} 0 R >> >> /Contents ${contentId} 0 R >>`);
      pageIds.push(pageId);
    }

    objects[pagesId - 1] = `<< /Type /Pages /Kids [${pageIds.map((id) => `${id} 0 R`).join(" ")}] /Count ${pageIds.length} >>`;

    const chunks = ["%PDF-1.4\n%\xE2\xE3\xCF\xD3\n"];
    const offsets = [0];
    for (let i = 0; i < objects.length; i += 1) {
      offsets.push(Buffer.byteLength(chunks.join(""), "binary"));
      chunks.push(`${i + 1} 0 obj\n${objects[i]}\nendobj\n`);
    }
    const xrefOffset = Buffer.byteLength(chunks.join(""), "binary");
    chunks.push(`xref\n0 ${objects.length + 1}\n`);
    chunks.push("0000000000 65535 f \n");
    for (let i = 1; i < offsets.length; i += 1) {
      chunks.push(`${String(offsets[i]).padStart(10, "0")} 00000 n \n`);
    }
    chunks.push(`trailer\n<< /Size ${objects.length + 1} /Root ${catalogId} 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`);
    return Buffer.from(chunks.join(""), "binary");
  }
}

export const pdfLayout = {
  marginX: MARGIN_X,
  bodySize: BODY_SIZE,
  bodyLeading: BODY_LEADING,
  smallSize: SMALL_SIZE,
  smallLeading: SMALL_LEADING,
  chapterSize: CHAPTER_SIZE,
  chapterLeading: CHAPTER_LEADING,
  bookSize: BOOK_SIZE,
  bookLeading: BOOK_LEADING,
};
