import { useEffect, useId, useRef } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

/**
 * The single modal shell for the app (design.md).
 * Plain dark overlay (no backdrop-blur), hairline paper2 panel.
 *
 * Props:
 *  - isOpen / onClose
 *  - title (string, rendered lowercase serif) — optional
 *  - eyebrow (string, mono UPPERCASE micro label above title) — optional
 *  - size: 'sm' | 'md' | 'lg' | 'xl' | 'editor' (max width; default 'md')
 *  - embedded: render an inline editor section inside an existing dialog
 *  - children: body content
 *  - footer: optional node pinned under the body
 *  - hideClose: hide the X button
 */
const SIZES = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-2xl',
  xl: 'max-w-5xl',
  editor: 'max-w-[1440px]',
};

export default function Modal({ isOpen, onClose, title, eyebrow, size = 'md', children, footer, hideClose = false, embedded = false, className = '', bodyClassName = '' }) {
  const panel=useRef(null);
  const titleId=useId();
  const closeRef=useRef(onClose);
  closeRef.current=onClose;
  useEffect(()=>{
    if(!isOpen || embedded) return;
    const previous=document.activeElement;
    const focusables=()=>[...(panel.current?.querySelectorAll('button, input, textarea, select, a[href], [tabindex="0"]') || [])].filter(el=>!el.disabled && el.getClientRects().length>0);
    const target=panel.current?.querySelector('[data-autofocus]') || panel.current?.querySelector('textarea, input:not([type=radio])') || focusables()[0];
    target?.focus();
    const keydown=e=>{
      if(e.key==='Escape'){e.preventDefault();e.stopPropagation();closeRef.current?.();}
      if(e.key==='Tab'){
        const items=focusables(),first=items[0],last=items[items.length-1];
        if(e.shiftKey && document.activeElement===first){e.preventDefault();last?.focus();}
        else if(!e.shiftKey && document.activeElement===last){e.preventDefault();first?.focus();}
      }
    };
    document.addEventListener('keydown',keydown);
    return ()=>{document.removeEventListener('keydown',keydown);if(previous?.isConnected)previous.focus();};
  },[isOpen,embedded]);
  if (!isOpen) return null;

  if (embedded) return <div className={`modal-embedded relative w-full min-h-0 flex flex-col ${className}`}>
    {(title || eyebrow) && <div className="modal-embedded-header px-4 sm:px-6 pt-3 sm:pt-6 pb-4 border-b border-rule shrink-0">
      {eyebrow && <p className="eyebrow mb-1.5">{eyebrow}</p>}
      {title && <h2 id={titleId} className="font-semibold text-xl sm:text-2xl text-ink leading-tight break-words">{title}</h2>}
    </div>}
    <div className={`modal-embedded-body px-4 sm:px-6 py-5 overflow-y-auto overscroll-contain custom-scrollbar grow ${bodyClassName}`}>{children}</div>
    {footer && <div className="modal-embedded-footer px-4 sm:px-6 py-4 border-t border-rule shrink-0">{footer}</div>}
  </div>;

  return createPortal(
    (
    /* Phone: a bottom sheet — anchored to the thumb, edge to edge, and free to
       run taller than a centred dialog whose 4px side margins wasted the only
       width there was. From sm up it is the centred dialog it always was. */
    <div
      className="local-modal fixed inset-0 z-[100] flex items-end sm:items-center justify-center bg-black/70 p-0 sm:p-4 animate-fade"
      onMouseDown={(e) => { if (e.target === e.currentTarget && onClose) onClose(); }}
      aria-labelledby={title ? titleId : undefined}
      role="dialog"
      aria-modal="true"
    >
      <div
        ref={panel}
        className={`card relative w-full ${SIZES[size] || SIZES.md} ${className} max-h-[92vh] sm:max-h-[90vh] flex flex-col
          rounded-b-none sm:rounded-card animate-sheet-up sm:animate-none`}
      >
        {/* Grab handle: the affordance that says "this sheet closes downward". */}
        <div className="sm:hidden pt-2.5 pb-1 flex justify-center shrink-0" aria-hidden="true">
          <span className="w-9 h-1 rounded-full bg-[color:var(--color-rule-2)]" />
        </div>
        {!hideClose && onClose && (
          <button
            onClick={onClose}
            aria-label="close"
            className="absolute top-3 right-3 sm:top-4 sm:right-4 z-10 p-2 rounded-full text-muted hover:text-ink hover:bg-paper3 transition-colors"
          >
            <X size={18} />
          </button>
        )}
        {(title || eyebrow) && (
          <div className="px-4 sm:px-6 pt-3 sm:pt-6 pb-4 border-b border-rule shrink-0">
            {eyebrow && <p className="eyebrow mb-1.5">{eyebrow}</p>}
            {title && <h2 id={titleId} className="font-semibold text-xl sm:text-2xl text-ink leading-tight break-words pr-10">{title}</h2>}
          </div>
        )}
        <div className={`px-4 sm:px-6 py-5 overflow-y-auto overscroll-contain custom-scrollbar grow ${bodyClassName}`}>
          {children}
        </div>
        {footer && (
          <div className="px-4 sm:px-6 py-4 border-t border-rule shrink-0 safe-bottom">
            {footer}
          </div>
        )}
        {!footer && <div className="sm:hidden safe-bottom" />}
      </div>
    </div>), document.body
  );
}
