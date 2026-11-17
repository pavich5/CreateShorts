import React from 'react';
import { CAPTION_STYLES } from './captionStyles';

export default function CaptionStylePicker({value, onChange, disabled=false, name='caption-preset'}) {
  return <div className="caption-style-grid">
    {CAPTION_STYLES.map(p => <label key={p.id} className={`caption-style-choice ${value===p.id ? 'is-selected' : ''}`}>
      <input type="radio" name={name} value={p.id} checked={value===p.id} onChange={() => onChange(p.id)} disabled={disabled} className="sr-only" />
      <span className="caption-style-sample" aria-hidden="true" style={{fontFamily:`"${p.fontName}", Arial, sans-serif`}}>
        {p.id==='cc_none' ? <span className="text-slate-400 text-3xl font-normal">∅</span> : p.id==='oneword' ? <span className="text-2xl">CREATE</span> :
          <span><span style={{opacity:p.id==='cc_negative_keywords' ? .5 : 1}}>GREAT </span><span className="rounded px-1" style={{color:p.box && p.id==='lime' ? '#111827' : p.box ? '#fff' : p.color,background:p.box ? p.color : 'transparent'}}>CLIPS</span></span>}
      </span>
      <span className="block text-xs font-semibold text-ink mt-2">{p.label}</span>
      <span className="block text-[11px] text-muted mt-1 leading-snug">{p.hint}</span>
    </label>)}
  </div>;
}
