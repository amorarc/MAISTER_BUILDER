/**
 * The agent answers in light markdown. This renders the subset it actually
 * emits - headings, bullet and numbered lists, fenced and inline code, bold and
 * italic - as React elements rather than raw HTML, so nothing the model writes
 * can inject markup.
 */

const INLINE = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)|(_[^_\n]+_)/g;

function inline(text) {
  const out = [];
  let last = 0;
  let m;

  INLINE.lastIndex = 0;
  while ((m = INLINE.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const token = m[0];
    const key = `${m.index}`;
    if (token.startsWith("`")) out.push(<code key={key}>{token.slice(1, -1)}</code>);
    else if (token.startsWith("**")) out.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    else out.push(<em key={key}>{token.slice(1, -1)}</em>);
    last = m.index + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const BULLET = /^\s*[-*+]\s+(.*)$/;
const NUMBER = /^\s*\d+[.)]\s+(.*)$/;
const HEADING = /^(#{1,4})\s+(.*)$/;

/** Group lines into blocks: fences, headings, runs of list items, paragraphs. */
function blocks(text) {
  const lines = (text || "").split("\n");
  const out = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (line.trimStart().startsWith("```")) {
      const body = [];
      i += 1;
      while (i < lines.length && !lines[i].trimStart().startsWith("```")) {
        body.push(lines[i]);
        i += 1;
      }
      i += 1; // closing fence
      out.push({ type: "code", body: body.join("\n") });
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      out.push({ type: "heading", level: heading[1].length, body: heading[2] });
      i += 1;
      continue;
    }

    const isItem = (l) => BULLET.exec(l) || NUMBER.exec(l);
    if (isItem(line)) {
      const ordered = !!NUMBER.exec(line);
      const items = [];
      while (i < lines.length && isItem(lines[i])) {
        const m = BULLET.exec(lines[i]) || NUMBER.exec(lines[i]);
        items.push(m[1]);
        i += 1;
      }
      out.push({ type: "list", ordered, items });
      continue;
    }

    if (!line.trim()) {
      i += 1;
      continue;
    }

    const para = [];
    while (i < lines.length && lines[i].trim() && !isItem(lines[i]) &&
           !HEADING.exec(lines[i]) && !lines[i].trimStart().startsWith("```")) {
      para.push(lines[i]);
      i += 1;
    }
    out.push({ type: "para", body: para.join("\n") });
  }

  return out;
}

export default function Markdown({ text, className = "" }) {
  return (
    <div className={`md ${className}`}>
      {blocks(text).map((b, i) => {
        if (b.type === "code") return <pre key={i}>{b.body}</pre>;
        if (b.type === "heading") {
          const Tag = `h${Math.min(b.level + 2, 6)}`;
          return <Tag key={i}>{inline(b.body)}</Tag>;
        }
        if (b.type === "list") {
          const Tag = b.ordered ? "ol" : "ul";
          return (
            <Tag key={i}>
              {b.items.map((item, j) => (
                <li key={j}>{inline(item)}</li>
              ))}
            </Tag>
          );
        }
        return <p key={i}>{inline(b.body)}</p>;
      })}
    </div>
  );
}
