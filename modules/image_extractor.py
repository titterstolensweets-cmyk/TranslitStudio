"""
═══════════════════════════════════════════════════════════════════════════════
Image Extractor Module for Supervertaler
═══════════════════════════════════════════════════════════════════════════════

Purpose:
    Extract images from DOCX files and save them as PNG files. Where possible,
    detect each image's label from the document (Figure 7, Table 3, FIG. 6a,
    Plate IV, …) and use that as the filename. Falls back to sequential
    numbering when no label is detectable.

How labels are detected (Option A, v1.10.190):
    1. The image's own paragraph is inspected. If it carries Word's built-in
       **Caption** paragraph style (`<w:pStyle w:val="Caption"/>`), its text
       is used directly.
    2. Failing that, the next paragraph (most common caption position) is
       checked the same way — Caption-styled wins, otherwise pattern match.
    3. The previous paragraph is checked last (less common, but happens for
       documents where the caption sits *above* the figure).
    4. If none of those produce a label, the image gets a sequential fallback
       filename (``Fig. N.png``) and the file is still extracted, just less
       informatively named.

    Pattern detection matches the common label vocabularies:

        FIG. 7 / FIGS. 6-8       — patent figures
        Figure 7 / Figs 7-8      — academic / general figures
        Table 3                  — tables
        Diagram 4                — engineering diagrams
        Chart 2                  — business charts
        Photo 5 / Photograph 5   — photography
        Scheme 1                 — chemistry reaction schemes
        Plate IV / Plate 12      — botany / older scientific work
        Exhibit A / Exhibit 3    — legal documents

    The matched label is preserved verbatim — "FIG. 7" stays "FIG. 7.png",
    "Figure 7" stays "Figure 7.png" — so the filename matches what the
    reader sees in the document.

v1.10.189 (previous): switched from ZIP namelist order (broken lexical sort
that produced ``Fig. 7.png`` containing the document's 15th image) to
document order via word/document.xml + relationship parsing.

Author: Supervertaler Development Team
Created: 2025-11-17
Last Modified: 2026-05-26 (v1.10.190)

═══════════════════════════════════════════════════════════════════════════════
"""

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Tuple, Optional, Dict
from zipfile import ZipFile
from io import BytesIO
from PIL import Image


# ── XML namespace tags ──────────────────────────────────────────────────
# Pre-bound for speed and readability inside the parser.
_NS_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_NS_R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
_NS_A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
_NS_V = '{urn:schemas-microsoft-com:vml}'

_TAG_P    = _NS_W + 'p'
_TAG_T    = _NS_W + 't'
_TAG_PPR  = _NS_W + 'pPr'
_TAG_PSTYLE = _NS_W + 'pStyle'
_ATTR_W_VAL = _NS_W + 'val'
_TAG_BLIP = _NS_A + 'blip'
_ATTR_R_EMBED = _NS_R + 'embed'
_ATTR_R_LINK  = _NS_R + 'link'
_ATTR_R_ID    = _NS_R + 'id'
_TAG_IMAGEDATA = _NS_V + 'imagedata'

# Paragraph style names that count as "caption" (matched case-insensitively).
# Word's English UI ships "Caption"; non-English Words use localised
# style names but the underlying styleId is usually still "Caption".
# A few documents use custom styles like "FigureLegend" / "TableLegend".
_CAPTION_STYLE_IDS = {
    'caption', 'figurecaption', 'figurelegend',
    'tablecaption', 'tablelegend',
}

# Relationship-file regex. Used only on the rels XML, not document.xml.
_REL_RE = re.compile(r'<Relationship\s+[^>]*Id="([^"]+)"[^>]*Target="([^"]+)"')


# ── Label-detection patterns ────────────────────────────────────────────
# Each pattern captures the full label text (e.g. "Figure 7", "FIG. 6a")
# so we can preserve the document's exact spelling/capitalisation in the
# filename. Ordered roughly by specificity — patent FIG. first, then
# academic Figure, then tables, etc.
#
# Pattern shape:
#   \b(<kind>(\.|\s)+<id>)\b
# where <id> is one or more digits optionally followed by a single
# letter (1, 7, 6a, 12B), OR — for Plate — Roman numerals.
_LABEL_PATTERNS = [
    # Patent style: FIG. 7, FIGS. 6, FIG.7, FIG 7
    re.compile(r'\b(FIGS?\.?\s*\d+[A-Za-z]?)\b', re.IGNORECASE),
    # Academic / generic: Figure 7, Figures 6-8 (we capture "Figure 6"),
    # Fig 7, Fig. 7
    re.compile(r'\b(Figures?\s+\d+[A-Za-z]?)\b', re.IGNORECASE),
    re.compile(r'\b(Fig\.?\s+\d+[A-Za-z]?)\b', re.IGNORECASE),
    # Tables
    re.compile(r'\b(Tables?\s+\d+[A-Za-z]?)\b', re.IGNORECASE),
    # Engineering diagrams
    re.compile(r'\b(Diagrams?\s+\d+[A-Za-z]?)\b', re.IGNORECASE),
    # Business charts
    re.compile(r'\b(Charts?\s+\d+[A-Za-z]?)\b', re.IGNORECASE),
    # Photos / photographs
    re.compile(r'\b(Photo(?:graph)?s?\s+\d+[A-Za-z]?)\b', re.IGNORECASE),
    # Chemistry schemes
    re.compile(r'\b(Schemes?\s+\d+[A-Za-z]?)\b', re.IGNORECASE),
    # Plates — accept Roman numerals (older scientific tradition) or digits
    re.compile(r'\b(Plates?\s+(?:\d+|[IVXLCM]+))\b', re.IGNORECASE),
    # Legal exhibits — letter-or-digit ID
    re.compile(r'\b(Exhibits?\s+[A-Za-z]?\d*[A-Za-z]?)\b', re.IGNORECASE),
]

# Characters not allowed in Windows / cross-platform filenames.
_FS_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────

def _direct_paragraph_text(p) -> str:
    """Return the text of paragraph ``p`` excluding text from any nested
    ``<w:p>`` (text boxes, embedded paragraphs). Word allows paragraphs
    inside textboxes that themselves live inside paragraphs; if we just
    used ``itertext()`` on the outer paragraph we'd pick up the textbox
    paragraph's text too and pollute the outer's label-detection
    candidates. This walker visits only descendants that don't sit under
    a nested ``<w:p>`` ancestor.
    """
    parts: List[str] = []

    def _walk(el):
        for child in el:
            if child.tag == _TAG_P:
                continue  # skip nested paragraphs entirely
            if child.tag == _TAG_T and child.text:
                parts.append(child.text)
            _walk(child)

    if p.text:
        parts.append(p.text)
    _walk(p)
    return ''.join(parts).strip()


def _paragraph_image_rids(p) -> List[str]:
    """Return image relationship IDs (``r:embed``, ``r:link``, ``r:id``
    from VML ``v:imagedata``) referenced by paragraph ``p``. As with
    ``_direct_paragraph_text`` we skip references that sit inside a
    nested ``<w:p>``."""
    rids: List[str] = []

    def _walk(el):
        for child in el:
            if child.tag == _TAG_P:
                continue
            if child.tag == _TAG_BLIP:
                rid = child.get(_ATTR_R_EMBED) or child.get(_ATTR_R_LINK)
                if rid:
                    rids.append(rid)
            elif child.tag == _TAG_IMAGEDATA:
                rid = child.get(_ATTR_R_ID)
                if rid:
                    rids.append(rid)
            _walk(child)

    _walk(p)
    return rids


def _paragraph_is_caption(p) -> bool:
    """Does paragraph ``p`` carry the Word built-in Caption paragraph
    style (or one of the common synonyms)?"""
    ppr = p.find(_TAG_PPR)
    if ppr is None:
        return False
    pstyle = ppr.find(_TAG_PSTYLE)
    if pstyle is None:
        return False
    val = (pstyle.get(_ATTR_W_VAL) or '').strip().lower()
    return val in _CAPTION_STYLE_IDS


def _detect_label_in_text(text: str) -> Optional[str]:
    """Search ``text`` for any of the figure / table / etc. label
    patterns. Returns the first matched label string, verbatim, or None.
    """
    if not text:
        return None
    for pat in _LABEL_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1).strip()
    return None


def _detect_label(paragraphs: List[dict], p_idx: int) -> Optional[str]:
    """Try to find a label for the image in paragraphs[p_idx].

    Detection rules (v1.10.191 — position dominates style):

    - The **previous** paragraph is only a valid caption candidate when
      the paragraph BEFORE it doesn't itself contain an image. Patent-
      style layouts emit ``[image][caption][image][caption]…`` — in
      that case the paragraph immediately before an image is the
      previous image's caption, not the current image's. We detect
      this by peeking two paragraphs back.

    - Within the allowed candidates, position order is
      ``same → next → prev``. At each position we prefer
      Caption-styled paragraphs (they're authoritative for that
      position), then fall back to pattern matching in body text.

    Previous versions promoted ANY Caption-styled paragraph (same /
    next / prev) above any pattern-matched body text, which mis-
    attributed cases like this:
        p[N-1]  "FIG. 16"  [Caption-styled, belongs to image N-2]
        p[N]    <image of FIG. 17>
        p[N+1]  "FIG. 17"  [NOT Caption-styled]
    The previous code labelled the image at p[N] as "FIG. 16" because
    p[N-1] won on style. v1.10.191 ignores p[N-1] entirely here
    (since p[N-2] is an image), and finds the correct "FIG. 17" by
    pattern-matching p[N+1].
    """
    same = paragraphs[p_idx]
    nxt = paragraphs[p_idx + 1] if p_idx + 1 < len(paragraphs) else None
    prv = paragraphs[p_idx - 1] if p_idx > 0 else None
    prv_prv = paragraphs[p_idx - 2] if p_idx >= 2 else None

    # Suppress previous-paragraph candidacy when it sits between two
    # images — it belongs to the earlier image, not this one. A None
    # prv_prv (start of document) is fine; an empty rids list at
    # prv_prv is also fine (just regular text leading into the image).
    prev_belongs_to_us = (prv is not None) and (
        prv_prv is None or not prv_prv.get('rids')
    )

    # Build the candidate list in position priority order.
    candidates: List[dict] = [same]
    if nxt is not None:
        candidates.append(nxt)
    if prev_belongs_to_us:
        candidates.append(prv)

    # At each position, run all three strategies in order before
    # moving to the next position:
    #   (a) Caption-styled + pattern match  →  best signal
    #   (b) Caption-styled + leading-text fallback (capped at 80 chars)
    #   (c) Body-text pattern match
    # The early-return inside the loop guarantees that closer
    # paragraphs always beat farther ones, regardless of style.
    for cand in candidates:
        if cand is None:
            continue
        text = cand.get('text') or ''
        if not text:
            continue

        is_cap = cand.get('is_caption', False)

        # (a) Pattern match — works for both caption-styled and body
        # text. The match itself is the strongest possible signal
        # (the literal label string is right there), so we accept it
        # at any position regardless of style.
        label = _detect_label_in_text(text)
        if label:
            return label

        # (b) Caption-styled but no pattern match — use the leading
        # sentence as the filename ("Hydraulic system overview" rather
        # than "Fig. 7"). Only applied to Caption-styled paragraphs;
        # plain body text without a recognisable label pattern is too
        # unreliable.
        if is_cap:
            leading = re.split(r'[.,:;\n]', text)[0].strip()
            if leading:
                return leading[:80].strip()

    return None


def _sanitize_label(label: str) -> str:
    """Strip filesystem-illegal characters and trailing punctuation /
    whitespace from a candidate filename stem."""
    cleaned = _FS_ILLEGAL.sub('', label)
    cleaned = cleaned.strip()
    # Trailing punctuation other than balanced brackets, parens
    cleaned = re.sub(r'[\.\,\;\:\-\s]+$', '', cleaned)
    return cleaned


def _unique_filename(base_stem: str, used: set, ext: str = '.png') -> str:
    """Return ``base_stem + ext`` if unused; otherwise append (2), (3) …"""
    name = base_stem + ext
    if name not in used:
        used.add(name)
        return name
    n = 2
    while f"{base_stem} ({n}){ext}" in used:
        n += 1
    name = f"{base_stem} ({n}){ext}"
    used.add(name)
    return name


def _natural_key(s: str):
    """Sort key that treats embedded digits as integers — keeps
    image2.png before image10.png. Used for the fallback path when
    document-order parsing fails."""
    return [int(x) if x.isdigit() else x.lower()
            for x in re.split(r'(\d+)', s)]


def _parse_paragraphs(doc_xml_bytes: bytes) -> List[dict]:
    """Parse document.xml into an ordered list of paragraph dicts:

        {'text': str, 'is_caption': bool, 'rids': [str]}

    Order is document order. Nested paragraphs (inside textboxes etc)
    are included as their own entries, but their text/rids don't bleed
    into their ancestor paragraph (see ``_direct_paragraph_text``)."""
    root = ET.fromstring(doc_xml_bytes)
    paragraphs: List[dict] = []
    # iter() walks depth-first in document order.
    for p in root.iter(_TAG_P):
        paragraphs.append({
            'text': _direct_paragraph_text(p),
            'is_caption': _paragraph_is_caption(p),
            'rids': _paragraph_image_rids(p),
        })
    return paragraphs


def _build_rel_map(rels_xml_bytes: bytes) -> Dict[str, str]:
    """Parse a relationships file and return {rId: media_path} (only
    entries whose Target is under ``media/``). The returned path is
    ZIP-rooted, e.g. ``word/media/image3.png``."""
    rels_text = rels_xml_bytes.decode('utf-8', errors='replace')
    rel_map: Dict[str, str] = {}
    for rid, target in _REL_RE.findall(rels_text):
        t = target.lstrip('./')
        if t.startswith('media/'):
            rel_map[rid] = 'word/' + t
    return rel_map


def _collect_surrounding_text(paragraphs: List[dict], p_idx: int,
                              window: int = 2) -> str:
    """Return up to ``window`` paragraphs of text on either side of the
    given paragraph, joined with newlines. Used as the surrounding-text
    snippet fed to the vision AI fallback. Empty paragraphs are skipped
    but still count against the window so we don't drift arbitrarily
    far from the image."""
    start = max(0, p_idx - window)
    end = min(len(paragraphs), p_idx + window + 1)
    parts = []
    for i in range(start, end):
        t = (paragraphs[i].get('text') or '').strip()
        if t:
            parts.append(t)
    return '\n'.join(parts)


def _get_body_images_with_labels(
    zip_ref: ZipFile,
) -> List[Tuple[str, Optional[str], str]]:
    """Return list of (media_path, detected_label, surrounding_text) in
    document order.

    ``detected_label`` may be ``None`` if no label could be detected from
    pattern + Caption-style scanning — caller should fall back to either
    a vision-AI label step (v1.10.192) or sequential numbering for those
    images. ``surrounding_text`` is a ±2-paragraph snippet around each
    image's paragraph; it's always populated (even if empty string) so
    the caller can feed it into an AI prompt without re-parsing the DOCX.

    Duplicate media files (same image referenced multiple times) are
    collapsed by first occurrence — the first-occurrence position
    determines the label.
    """
    try:
        rels_bytes = zip_ref.read('word/_rels/document.xml.rels')
        rel_map = _build_rel_map(rels_bytes)
        doc_bytes = zip_ref.read('word/document.xml')
        paragraphs = _parse_paragraphs(doc_bytes)
    except (KeyError, ET.ParseError):
        return []
    except Exception:
        return []

    out: List[Tuple[str, Optional[str], str]] = []
    seen_media = set()

    for p_idx, p in enumerate(paragraphs):
        if not p['rids']:
            continue
        # Detect label once per paragraph — all images in this paragraph
        # share the same caption context. If the paragraph has multiple
        # images, they'll be disambiguated downstream via the (2), (3) …
        # suffix collision handler.
        label = _detect_label(paragraphs, p_idx)
        # Surrounding-text snippet, computed once per paragraph for the
        # same reason. v1.10.192: passed to the optional AI-label
        # callback when text-pattern detection produced no label.
        surrounding = _collect_surrounding_text(paragraphs, p_idx)
        for rid in p['rids']:
            media = rel_map.get(rid)
            if not media or media in seen_media:
                continue
            seen_media.add(media)
            out.append((media, label, surrounding))

    return out


def _process_image_bytes(img_bytes: bytes) -> Optional[Image.Image]:
    """Decode raw image bytes and return a PIL Image converted to RGB so
    it's safe to save as PNG. Returns None if decoding fails."""
    try:
        img = Image.open(BytesIO(img_bytes))
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(
                img,
                mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None
            )
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        return img
    except Exception:
        return None


# ────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────

class ImageExtractor:
    """Extract images from DOCX files and save as PNG, in document order,
    with detected captions used as filenames where possible.

    v1.10.192: optional vision-AI label detection. Pass an
    ``ai_label_fn`` callback into either extract method to have it
    invoked for each image that text-pattern detection couldn't label.
    The callback receives the image bytes, the image's MIME type
    (inferred from extension), and a snippet of surrounding text from
    the document; it returns a label string or ``None`` (meaning "AI
    couldn't tell either — use sequential fallback").
    """

    def __init__(self):
        self.supported_formats = ['.docx']

    # ── New v1.10.192 helpers ────────────────────────────────────────

    def peek_labels(self, docx_path: str) -> List[Tuple[str, Optional[str], str]]:
        """Return ``[(media_internal_path, detected_label, surrounding_text), …]``
        for a single DOCX without extracting anything. Use this to
        count how many images would need AI fallback (for cost
        estimation) before deciding whether to enable it.
        """
        if not os.path.exists(docx_path):
            raise FileNotFoundError(f"DOCX file not found: {docx_path}")
        with ZipFile(docx_path, 'r') as zip_ref:
            ordered = _get_body_images_with_labels(zip_ref)
            if ordered:
                return ordered
            # Fallback path (when document.xml parsing fails) — no
            # surrounding text available for the AI step but we still
            # populate the slot with an empty string so the tuple
            # arity is stable.
            raw = [f for f in zip_ref.namelist()
                   if f.startswith('word/media/')]
            return [(p, None, '') for p in sorted(raw, key=_natural_key)]

    def peek_labels_multiple(self, docx_paths: List[str]
                             ) -> List[Tuple[str, List[Tuple[str, Optional[str], str]]]]:
        """Multi-file peek. Returns a list of (docx_path, peek_result)
        tuples in input order. Missing files / unreadable DOCX entries
        are silently dropped — the caller's totals naturally reflect
        only the readable subset."""
        results = []
        for path in docx_paths:
            try:
                results.append((path, self.peek_labels(path)))
            except Exception:
                continue
        return results

    # ── Extraction methods ───────────────────────────────────────────

    def extract_images_from_docx(self, docx_path: str, output_dir: str,
                                 prefix: str = "Fig.",
                                 ai_label_fn=None) -> Tuple[int, List[str]]:
        """Extract images from a single DOCX in document order.

        Args:
            docx_path: Path to the DOCX file
            output_dir: Directory where images will be saved
            prefix: Fallback filename prefix when no caption is detected
                AND no AI fallback resolves the label
                (default ``"Fig."`` → ``Fig. 1.png``, ``Fig. 2.png`` …)
            ai_label_fn: Optional callback invoked for every image that
                text-pattern detection couldn't label. Signature:
                    ``fn(image_bytes: bytes, surrounding_text: str,
                         media_path: str) -> Optional[str]``
                Returning ``None`` keeps the sequential fallback name;
                returning a non-empty string uses that string as the
                filename (sanitised + dedupe-suffixed as usual).

        Returns:
            (number of images extracted, list of output file paths)
        """
        if not os.path.exists(docx_path):
            raise FileNotFoundError(f"DOCX file not found: {docx_path}")
        if not docx_path.lower().endswith('.docx'):
            raise ValueError("File must be a DOCX document")

        os.makedirs(output_dir, exist_ok=True)

        extracted_files: List[str] = []
        try:
            with ZipFile(docx_path, 'r') as zip_ref:
                ordered = _get_body_images_with_labels(zip_ref)

                # Fallback when document-order parsing produced nothing.
                if not ordered:
                    raw = [f for f in zip_ref.namelist()
                           if f.startswith('word/media/')]
                    ordered = [(p, None, '') for p in sorted(raw, key=_natural_key)]

                used_names: set = set()
                # Track sequential fallback counter independently of
                # detected labels — both end up in `used_names` to
                # prevent collisions.
                fallback_counter = 0
                for media, label, surrounding in ordered:
                    try:
                        img_bytes = zip_ref.read(media)
                    except KeyError:
                        continue
                    img = _process_image_bytes(img_bytes)
                    if img is None:
                        print(f"Warning: Could not process image {media}")
                        continue

                    # v1.10.192: if text-pattern detection gave us no
                    # label AND the caller passed an AI fallback,
                    # let the AI have a go before falling back to
                    # sequential numbering.
                    if not label and ai_label_fn is not None:
                        try:
                            ai_label = ai_label_fn(img_bytes, surrounding, media)
                            if ai_label and isinstance(ai_label, str):
                                label = ai_label.strip()
                        except Exception as e:
                            print(f"Warning: AI label callback failed for {media}: {e}")

                    if label:
                        stem = _sanitize_label(label)
                    else:
                        fallback_counter += 1
                        stem = f"{prefix} {fallback_counter}"

                    out_name = _unique_filename(stem, used_names)
                    out_path = os.path.join(output_dir, out_name)
                    img.save(out_path, 'PNG', optimize=True)
                    extracted_files.append(out_path)
        except Exception as e:
            raise Exception(f"Error extracting images: {e}")

        return len(extracted_files), extracted_files

    def extract_from_multiple_docx(self, docx_paths: List[str], output_dir: str,
                                   prefix: str = "Fig.",
                                   ai_label_fn=None) -> Tuple[int, List[str]]:
        """Extract images from multiple DOCX files into one folder.

        Per-file label detection runs independently — labels and the
        fallback counter both restart for each file, but the global
        filename-uniqueness set is shared. If the same label appears in
        two files (e.g. both have a "Figure 1"), the second one becomes
        "Figure 1 (2).png".

        ``ai_label_fn`` works the same as in :meth:`extract_images_from_docx`
        and is invoked across files."""
        os.makedirs(output_dir, exist_ok=True)

        all_extracted_files: List[str] = []
        used_names: set = set()

        for docx_path in docx_paths:
            try:
                with ZipFile(docx_path, 'r') as zip_ref:
                    ordered = _get_body_images_with_labels(zip_ref)
                    if not ordered:
                        raw = [f for f in zip_ref.namelist()
                               if f.startswith('word/media/')]
                        ordered = [(p, None, '') for p in sorted(raw, key=_natural_key)]

                    fallback_counter = 0
                    for media, label, surrounding in ordered:
                        try:
                            img_bytes = zip_ref.read(media)
                        except KeyError:
                            continue
                        img = _process_image_bytes(img_bytes)
                        if img is None:
                            print(f"Warning: Could not process image from {docx_path}")
                            continue

                        if not label and ai_label_fn is not None:
                            try:
                                ai_label = ai_label_fn(img_bytes, surrounding, media)
                                if ai_label and isinstance(ai_label, str):
                                    label = ai_label.strip()
                            except Exception as e:
                                print(f"Warning: AI label callback failed for {media}: {e}")

                        if label:
                            stem = _sanitize_label(label)
                        else:
                            fallback_counter += 1
                            stem = f"{prefix} {fallback_counter}"

                        out_name = _unique_filename(stem, used_names)
                        out_path = os.path.join(output_dir, out_name)
                        img.save(out_path, 'PNG', optimize=True)
                        all_extracted_files.append(out_path)
            except Exception as e:
                print(f"Warning: Could not process file {docx_path}: {e}")
                continue

        return len(all_extracted_files), all_extracted_files


# Standalone usage example
if __name__ == "__main__":
    extractor = ImageExtractor()

    docx_file = "example.docx"
    output_directory = "extracted_images"

    if os.path.exists(docx_file):
        count, files = extractor.extract_images_from_docx(docx_file, output_directory)
        print(f"Extracted {count} images:")
        for f in files:
            print(f"  - {f}")
    else:
        print(f"File not found: {docx_file}")
