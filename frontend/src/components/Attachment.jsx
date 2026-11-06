import { useEffect, useState } from "react";
import { attachmentUrl, formatSize } from "../lib/files.js";
import { FileIcon } from "./Icons.jsx";

/** Shows one uploaded file: a thumbnail for photos, a download chip for other files. */
export default function Attachment({ att, token }) {
  const [url, setUrl] = useState(att.localUrl || null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (att.localUrl || !token || typeof att.id !== "number") return;
    let live = true;
    attachmentUrl(att, token)
      .then((u) => live && setUrl(u))
      .catch(() => live && setFailed(true));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [att.id, att.key, att.localUrl, token]);

  if (att.kind === "image" && !failed) {
    return url ? (
      <a className="att-image" href={url} target="_blank" rel="noreferrer" title={att.filename}>
        <img src={url} alt={att.filename} />
      </a>
    ) : <div className="att-image placeholder" aria-label={`Loading ${att.filename}`} />;
  }

  const body = (
    <>
      <span className="att-icon"><FileIcon width={18} height={18} /></span>
      <span className="att-text">
        <span className="att-name">{att.filename}</span>
        <span className="att-meta">{failed ? "Unavailable" : formatSize(att.size || 0)}</span>
      </span>
    </>
  );
  return url ? (
    <a className="att-file" href={url} download={att.filename}>{body}</a>
  ) : <div className="att-file">{body}</div>;
}
