import { CAPTION_STYLES as CAPTION_PRESETS } from './captionStyles';
import CaptionStylePicker from './CaptionStylePicker';
import React, { useState, useEffect, useMemo } from 'react';
import { Loader2 } from 'lucide-react';
import { apiFetch } from '../lib/api';
import { retimeCaptionText } from '../lib/manualCaptionWords';
import { savedOneWordPreference } from '../lib/captionPreferences';
import { useEditorDraft } from '../lib/useEditorDraft';
import RemotionPreview from './RemotionPreview';
import { NativeClipPreview } from './ClipPreview';
import Modal from './ui/Modal';
import SegmentedControl from './ui/SegmentedControl';

const FONT_OPTIONS = [
    { value: 'Anton', label: 'Anton' },
    { value: 'Montserrat ExtraBold', label: 'Montserrat' },
    { value: 'Verdana', label: 'Verdana' },
    { value: 'Arial', label: 'Arial' },
    { value: 'Impact', label: 'Impact' },
    { value: 'Helvetica', label: 'Helvetica' },
    { value: 'Georgia', label: 'Georgia' },
    { value: 'Courier New', label: 'Courier New' },
];

const COLOR_PRESETS = [
    { color: '#FFFFFF', label: 'White' },
    { color: '#FFFF00', label: 'Yellow' },
    { color: '#00FFFF', label: 'Cyan' },
    { color: '#00FF00', label: 'Green' },
    { color: '#FF0000', label: 'Red' },
    { color: '#FF69B4', label: 'Pink' },
];

const HIGHLIGHT_PRESETS = [
    { color: '#FFE500', label: 'Yellow' },
    { color: '#FFDD00', label: 'Gold' },
    { color: '#A3FF12', label: 'Lime' },
    { color: '#7C3AED', label: 'Purple' },
    { color: '#FF4444', label: 'Red' },
    { color: '#00FF88', label: 'Green' },
    { color: '#00BBFF', label: 'Blue' },
    { color: '#FF69B4', label: 'Pink' },
];

const ANIMATION_OPTIONS = [
    { value: 'pop', label: 'Pop' },
    { value: 'word-highlight', label: 'Glow' },
    { value: 'karaoke', label: 'Box' },
    { value: 'none', label: 'None' },
];

// Preview animation -> the effect burned server-side, so what the modal
// plays is what the clip gets. 'karaoke' in the preview is a box behind the
// active word, which is the 'highlight' effect.
const ANIMATION_TO_EFFECT = { pop: 'pop', 'word-highlight': 'glow', karaoke: 'highlight', none: 'none' };
const EFFECT_TO_ANIMATION = { pop: 'pop', glow: 'word-highlight', highlight: 'karaoke', box: 'karaoke', none: 'none' };

// Font size in the units /api/subtitle takes. M is what every clip ships
// with (subtitles.AUTO_CAPTION_STYLE font_size 44), so opening the modal and
// applying never shrinks the captions.
const SIZE_OPTIONS = [
    { value: 34, label: 'S' },
    { value: 44, label: 'M' },
    { value: 56, label: 'L' },
    { value: 70, label: 'XL' },
];

// Characters per line at size M, per font: Anton is condensed, Montserrat
// wide. Bigger text gets proportionally fewer, so a line still fits the 9:16
// frame instead of wrapping into a wall of text.
const LINE_CHARS = { Anton: 16, 'Montserrat ExtraBold': 9, Impact: 16 };
const lineBudget = (fontName, fontSize, oneWord) =>
    oneWord ? 1 : Math.max(6, Math.round((LINE_CHARS[fontName] || 14) * 44 / fontSize));

// libass sizes text against PlayResY 288 (subtitles.generate_ass): one unit
// there is ~3.85 CSS px in the 1080x1920 preview, measured on burned frames.
const PREVIEW_PX_PER_UNIT = 3.85;

const POSITION_OPTIONS = [
    { value: 'top', label: 'top' },
    { value: 'middle', label: 'middle' },
    { value: 'bottom', label: 'bottom' },
];

// Ready-made caption looks burned server-side as karaoke ASS (word highlight):
// dimmed base text + strong active word, optional glow/pop/box effect.


// Mirrors subtitles._luminance (Rec. 601).
const luminance = (hex) => {
    const m = /^#?([0-9a-fA-F]{6})$/.exec(hex || '');
    if (!m) return 1;
    const [r, g, b] = [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16));
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255;
};

const swatchClass = (selected) =>
    `w-6 h-6 rounded-full transition-all ${selected
        ? 'ring-2 ring-[color:var(--color-accent)] ring-offset-2 ring-offset-[color:var(--color-paper-2)]'
        : 'ring-1 ring-[color:var(--color-rule-2)] hover:ring-[color:var(--color-accent)]'}`;

export default function SubtitleModal({ isOpen, onClose, onGenerate, onApplyAll, onRemove, isProcessing, videoUrl, jobId, clipIndex, existingHook, layoutRanges = [], initialPreset = 'cc_mozi', initialStyle = null, bulkCount = 0, bulkProgress, embedded = false, active = true, onEditorStateChange }) {
    // Opens on the look the clip already has (the Default preset), so
    // "apply" without touching anything changes nothing.
    const [position, setPosition] = useState('bottom');
    const [fontSize, setFontSize] = useState(44);
    const [fontName, setFontName] = useState('Anton');
    const [fontColor, setFontColor] = useState('#FFFFFF');
    const [highlightColor, setHighlightColor] = useState('#FFE500');
    const [borderColor, setBorderColor] = useState('#000000');
    const [borderWidth, setBorderWidth] = useState(4);
    const [bgColor, setBgColor] = useState('#000000');
    const [bgOpacity, setBgOpacity] = useState(0.0);
    const [animation, setAnimationState] = useState('pop');
    const [showTextEditor, setShowTextEditor] = useState(false);

    // Karaoke (server-side ASS burn) state
    const [style, setStyle] = useState('karaoke'); // classic | karaoke
    const [effect, setEffect] = useState('pop'); // none | glow | pop | box | highlight
    const [baseOpacity, setBaseOpacity] = useState(1.0);
    const [uppercase, setUppercase] = useState(true);
    const [reveal, setReveal] = useState(false);
    const [shadow, setShadow] = useState(0);
    const [oneWord, setOneWord] = useState(false);
    const [activePreset, setActivePreset] = useState('cc_mozi');

    const setAnimation = (value) => {
        setAnimationState(value);
        if (style === 'karaoke') setEffect(ANIMATION_TO_EFFECT[value] || 'none');
    };

    const applyPreset = (p) => {
        setActivePreset(p.id);
        setStyle(p.style);
        setEffect(p.effect);
        setHighlightColor(p.highlightColor);
        setBaseOpacity(p.baseOpacity);
        setUppercase(p.uppercase);
        setFontName(p.fontName);
        setBorderWidth(p.borderWidth);
        setFontColor('#FFFFFF');
        setBgOpacity(0);
        setReveal(!!p.reveal);
        setShadow(p.shadow || 0);
        setOneWord(!!p.oneWord);
        if (p.fontSize) setFontSize(p.fontSize);
        setAnimationState(p.style === 'karaoke' ? (EFFECT_TO_ANIMATION[p.effect] || 'none') : 'none');
    };

    useEffect(() => {
        if (!isOpen) return;
        const preset = CAPTION_PRESETS.find(p => p.id === initialPreset) || CAPTION_PRESETS.find(p => p.id === 'cc_mozi');
        applyPreset(preset);
        if(initialStyle){
          const fields={position:setPosition,font_size:setFontSize,font_name:setFontName,font_color:setFontColor,border_color:setBorderColor,border_width:setBorderWidth,bg_color:setBgColor,bg_opacity:setBgOpacity,style:setStyle,effect:setEffect,base_opacity:setBaseOpacity,uppercase:setUppercase,highlight_color:setHighlightColor,reveal:setReveal,shadow:setShadow};
          Object.entries(fields).forEach(([key,set])=>{if(initialStyle[key]!=null)set(initialStyle[key]);});
          if (initialStyle.effect != null) setAnimationState(EFFECT_TO_ANIMATION[initialStyle.effect] || 'none');
        }
        setOneWord(savedOneWordPreference(initialStyle, preset));
    }, [isOpen, initialPreset, initialStyle]);

    const maxChars = lineBudget(fontName, fontSize, oneWord);
    // Same block duration as the auto captions (AUTO_CAPTION_STYLE).
    const maxDuration = 1.4;

    // Remotion preview state
    const [originalCaptions, setOriginalCaptions] = useState([]);
    const [editableText, setEditableText] = useState('');
    const [durationSec, setDurationSec] = useState(0);
    const [captionsLoading, setCaptionsLoading] = useState(false);
    const [initialLoaded, setInitialLoaded] = useState(false);
    const useRemotionPreview = Boolean(videoUrl && durationSec > 0);
    const originalText = originalCaptions.map(caption => caption.text).join(' ');
    const manualText = originalCaptions.length === 0;
    const captions = useMemo(() => originalCaptions.length && editableText.trim() === originalText.trim()
        ? originalCaptions : retimeCaptionText(editableText, durationSec, originalCaptions),
    [editableText, originalText, durationSec, originalCaptions]);
    const captionTextValid = captions.length > 0 && captions.length <= 2000;

    // Fetch word-level captions when modal opens
    useEffect(() => {
        if (!isOpen) return;
        if (!jobId || clipIndex === undefined) { setInitialLoaded(true); return; }

        const controller = new AbortController();
        setCaptionsLoading(true);
        setInitialLoaded(false);
        setDurationSec(0);
        apiFetch(`/api/clip/${jobId}/${clipIndex}/transcript`, { signal: controller.signal })
            .then((res) => res.ok ? res.json() : null)
            .then((data) => {
                if (controller.signal.aborted) return;
                if (Number.isFinite(data?.durationSec) && data.durationSec > 0) setDurationSec(data.durationSec);
                if (data && data.captions && data.captions.length > 0) {
                    setOriginalCaptions(data.captions);
                    setEditableText(data.captions.map(c => c.text).join(' '));
                } else {
                    setOriginalCaptions([]);
                    setEditableText('');
                    setShowTextEditor(true);
                }
            })
            .catch(() => {
                if (!controller.signal.aborted) {
                    setOriginalCaptions([]);
                    setEditableText('');
                    setShowTextEditor(true);
                }
            })
            .finally(() => { if (!controller.signal.aborted) { setCaptionsLoading(false); setInitialLoaded(true); } });
        return () => controller.abort();
    }, [isOpen, jobId, clipIndex, videoUrl]);

    const handleTextEdit = newText => setEditableText(newText);

    // Build subtitle config for Remotion
    const subtitleConfig = {
        captions: activePreset === 'cc_none' || effect === 'clipcutter:none' ? [] : captions,
        captionEffect: effect,
        wordsPerCaption: oneWord ? 1 : 4,
        layoutRanges,
        position,
        maxChars,
        maxDurationMs: maxDuration * 1000,
        style: {
            fontFamily: fontName,
            fontSize: effect.startsWith('clipcutter:') ? fontSize : Math.round(fontSize * 0.85 * PREVIEW_PX_PER_UNIT),
            fontColor,
            highlightColor,
            borderColor,
            borderWidth: borderWidth * (effect.startsWith('clipcutter:') ? 1 : 1.5),
            bgColor,
            bgOpacity,
            animation,
            // Karaoke look reflected live in the playable preview.
            baseOpacity: style === 'karaoke' ? baseOpacity : 1,
            uppercase: style === 'karaoke' ? uppercase : false,
            reveal: style === 'karaoke' && reveal,
            shadow: style === 'karaoke' ? shadow : 0,
            // Text on the active-word box: black on a light box, as burned.
            highlightTextColor: luminance(highlightColor) > 0.6 ? '#000000' : fontColor,
        },
    };

    const textEdited = manualText || editableText.trim() !== originalText.trim();
    const styleOptions = {
        position, fontSize, fontName, fontColor, borderColor, borderWidth, bgColor, bgOpacity,
        preset: activePreset, style, effect, baseOpacity, uppercase, highlightColor,
        reveal: !oneWord && reveal, shadow, maxChars, maxDuration,
        remotion: useRemotionPreview ? subtitleConfig : null,
        captions: textEdited ? captions : null,
    };
    const captionDraft = activePreset === 'cc_none' ? { preset: 'cc_none' } : {
        position, fontSize, fontName, fontColor, borderColor, borderWidth, bgColor, bgOpacity,
        preset: activePreset, style, effect, baseOpacity, uppercase, highlightColor,
        reveal: !oneWord && reveal, shadow, maxChars, maxDuration, oneWord, text: editableText.replace(/\s+/g, ' ').trim(),
    };
    const canApply = !isProcessing && !captionsLoading && (activePreset === 'cc_none' || captionTextValid);
    const saveCaptions = async () => {
        if (!canApply) return false;
        const saved = await onGenerate(styleOptions);
        if (saved !== true) return false;
        markSaved(captionDraft);
        return true;
    };
    const { markSaved } = useEditorDraft({
        value: captionDraft, ready: isOpen && initialLoaded && !captionsLoading,
        canSave: canApply, save: saveCaptions, onStateChange: onEditorStateChange,
    });
    const removeCaptions = async () => {
        if (isProcessing || !onRemove) return false;
        const saved = await onRemove();
        if (saved !== true) return false;
        setActivePreset('cc_none');
        markSaved({ preset: 'cc_none' });
        return true;
    };

    if (!isOpen) return null;

    return (
        <Modal isOpen={isOpen} embedded={embedded} onClose={onClose} size="xl" eyebrow="EDIT YOUR SHORT" title="Captions">
            <div className="caption-editor-layout flex flex-col md:flex-row gap-6">
                {/* Left: Preview */}
                <div className="caption-editor-preview">
                    {active && (captionsLoading ? (
                        <div className="flex items-center gap-2 text-muted">
                            <Loader2 size={16} className="animate-spin" />
                            <span className="text-sm lowercase">Loading preview...</span>
                        </div>
                    ) : useRemotionPreview ? (
                        <RemotionPreview
                            videoUrl={videoUrl}
                            durationInSeconds={durationSec}
                            subtitles={subtitleConfig}
                            hook={existingHook || null}
                        />
                    ) : (
                        <NativeClipPreview videoUrl={videoUrl} onLoadedMetadata={event => {
                            const actual = event.currentTarget.duration;
                            if (Number.isFinite(actual) && actual > 0) setDurationSec(actual);
                        }} />
                    ))}
                </div>

                {/* Right: Controls */}
                <div className="caption-editor-options w-full md:w-[440px] flex flex-col">
                    <div className="space-y-5 flex-1 overflow-y-auto custom-scrollbar pr-1">
                        {/* Caption presets (server-side karaoke burn) */}
                        <div>
                            <p className="text-sm font-semibold text-ink mb-3">Caption style</p>
                            <CaptionStylePicker value={activePreset} onChange={id=>applyPreset(CAPTION_PRESETS.find(p=>p.id===id))} disabled={isProcessing} name="editor-caption-preset" />
                        </div>
                        {activePreset === 'cc_none' && <p className="text-xs text-muted">Choose a caption style to edit its appearance.</p>}
                        <fieldset disabled={activePreset === 'cc_none'} className="space-y-5 min-w-0">
                        <div>
                            {style === 'karaoke' && (
                                <div className="mt-3 space-y-3 animate-fade">
                                    <div className="flex items-center justify-between">
                                        <span className="readout">UPPERCASE</span>
                                        <label className="relative inline-flex items-center cursor-pointer">
                                            <input type="checkbox" aria-label="Uppercase captions" checked={uppercase} onChange={(e) => setUppercase(e.target.checked)} className="sr-only peer" />
                                            <div className="w-8 h-4 rounded-full bg-paper3 peer-checked:bg-brass transition-colors after:content-[''] after:absolute after:top-0 after:left-0 after:h-4 after:w-4 after:rounded-full after:bg-ink after:transition-all peer-checked:after:translate-x-full"></div>
                                        </label>
                                    </div>
                                    <div>
                                        <div className="flex justify-between mb-1">
                                            <span className="readout">Dim inactive words</span>
                                            <span className="readout">{Math.round(baseOpacity * 100)}%</span>
                                        </div>
                                        <input
                                            aria-label="Dim inactive words"
                                            type="range"
                                            min="30"
                                            max="100"
                                            value={Math.round(baseOpacity * 100)}
                                            disabled={effect.startsWith('clipcutter:')}
                                            onChange={(e) => setBaseOpacity(parseInt(e.target.value) / 100)}
                                            className="w-full accent-[var(--color-accent)]"
                                        />
                                    </div>
                                </div>
                            )}
                        </div>

                        {/* Position Selector */}
                        <div>
                            <p className="eyebrow mb-2">Position</p>
                            <SegmentedControl
                                options={POSITION_OPTIONS}
                                value={position}
                                onChange={setPosition}
                                size="sm"
                            />
                        </div>

                        {/* Size */}
                        <div>
                            <p className="eyebrow mb-2">Size</p>
                            <SegmentedControl
                                options={SIZE_OPTIONS.some(o=>o.value===fontSize) ? SIZE_OPTIONS : [...SIZE_OPTIONS,{value:fontSize,label:String(fontSize)}]}
                                value={fontSize}
                                onChange={setFontSize}
                                size="sm"
                            />
                        </div>

                        {style === 'karaoke' && (
                            <div className="space-y-3">
                                <div className="flex items-center justify-between">
                                    <span className="readout">One word at a time</span>
                                    <label className="relative inline-flex items-center cursor-pointer">
                                        <input type="checkbox" aria-label="One word at a time" checked={oneWord} onChange={(e) => setOneWord(e.target.checked)} className="sr-only peer" />
                                        <div className="w-8 h-4 rounded-full bg-paper3 peer-checked:bg-brass transition-colors after:content-[''] after:absolute after:top-0 after:left-0 after:h-4 after:w-4 after:rounded-full after:bg-ink after:transition-all peer-checked:after:translate-x-full"></div>
                                    </label>
                                </div>
                                {!oneWord && (
                                    <div className="flex items-center justify-between">
                                        <span className="readout">Reveal word by word</span>
                                        <label className="relative inline-flex items-center cursor-pointer">
                                            <input type="checkbox" aria-label="Reveal word by word" disabled={effect.startsWith('clipcutter:')} checked={reveal} onChange={(e) => setReveal(e.target.checked)} className="sr-only peer" />
                                            <div className="w-8 h-4 rounded-full bg-paper3 peer-checked:bg-brass transition-colors after:content-[''] after:absolute after:top-0 after:left-0 after:h-4 after:w-4 after:rounded-full after:bg-ink after:transition-all peer-checked:after:translate-x-full"></div>
                                        </label>
                                    </div>
                                )}
                            </div>
                        )}

                        {effect.startsWith('clipcutter:') && <p className="text-xs text-muted">This caption style uses its own word emphasis. Choose Pop, Glow, Box, or None to adjust dimming and word reveal.</p>}
                        {/* Animation Style (new) */}
                        <div>
                            <p className="eyebrow mb-2">Animation</p>
                            <SegmentedControl
                                options={ANIMATION_OPTIONS}
                                value={animation}
                                onChange={setAnimation}
                                columns={2}
                                size="sm"
                            />
                        </div>

                        {/* Editable Transcript (collapsible) */}
                        {(
                            <div>
                                <button
                                    type="button"
                                    onClick={() => setShowTextEditor(!showTextEditor)}
                                    className="w-full flex items-center justify-between mb-2"
                                >
                                    <span className="eyebrow">{manualText ? 'Caption text' : 'Edit text'} ({captions.length} words)</span>
                                    <span className={`text-muted transition-transform ${showTextEditor ? 'rotate-180' : ''}`}>▾</span>
                                </button>
                                {manualText && <p className="text-xs text-muted leading-relaxed mb-3">This video has no transcript. Enter the caption text below; its words will be spaced evenly across the clip.</p>}
                                {(showTextEditor || manualText) && (
                                    <><textarea
                                        aria-label="Caption text"
                                        value={editableText}
                                        disabled={isProcessing || captionsLoading}
                                        onChange={(e) => handleTextEdit(e.target.value)}
                                        rows={5}
                                        dir="auto"
                                        className="input-field resize-none leading-relaxed animate-fade"
                                        placeholder={manualText ? 'Enter the words to show on your video…' : 'Edit subtitle text...'}
                                    />{editableText.trim() && !captionTextValid && durationSec > 0 && <p className="text-xs text-danger mt-2" role="alert">This text is too long. Use up to 2,000 words or shorten it for this clip.</p>}</>
                                )}
                            </div>
                        )}

                        {/* Font Family */}
                        <div>
                            <p className="eyebrow mb-2">Font</p>
                            <select
                                aria-label="Caption font"
                                value={fontName}
                                onChange={(e) => setFontName(e.target.value)}
                                className="input-field"
                            >
                                {FONT_OPTIONS.map((f) => (
                                    <option key={f.value} value={f.value} style={{ fontFamily: f.value }}>{f.label}</option>
                                ))}
                            </select>
                        </div>

                        {/* Text Color */}
                        <div>
                            <p className="eyebrow mb-2">Text color</p>
                            <div className="flex flex-wrap items-center gap-2.5">
                                {COLOR_PRESETS.map((c) => (
                                    <button
                                        key={c.color}
                                        onClick={() => setFontColor(c.color)}
                                        className={swatchClass(fontColor === c.color)}
                                        style={{ backgroundColor: c.color }}
                                        title={c.label}
                                        aria-pressed={fontColor === c.color}
                                    />
                                ))}
                                <label className="w-6 h-6 rounded-full border border-dashed border-rule2 cursor-pointer flex items-center justify-center hover:border-brass transition-colors overflow-hidden relative" title="Custom color">
                                    <span className="text-xs text-muted leading-none">+</span>
                                    <input type="color" aria-label="Caption text color" value={fontColor} onChange={(e) => setFontColor(e.target.value)} className="absolute inset-0 opacity-0 cursor-pointer" />
                                </label>
                            </div>
                        </div>

                        {/* Highlight Color (new) */}
                        <div>
                            <p className="eyebrow mb-2">Highlight</p>
                            <div className="flex flex-wrap items-center gap-2.5">
                                {HIGHLIGHT_PRESETS.map((c) => (
                                    <button
                                        key={c.color}
                                        onClick={() => setHighlightColor(c.color)}
                                        className={swatchClass(highlightColor === c.color)}
                                        style={{ backgroundColor: c.color }}
                                        title={c.label}
                                        aria-pressed={highlightColor === c.color}
                                    />
                                ))}
                            </div>
                        </div>

                        {/* Border / Outline */}
                        <div>
                            <p className="eyebrow mb-2">Border</p>
                            <div className="flex items-center gap-3">
                                <label className="relative w-8 h-8 rounded-input border border-rule2 cursor-pointer overflow-hidden shrink-0" title="Border color">
                                    <div className="w-full h-full" style={{ backgroundColor: borderColor }} />
                                    <input type="color" aria-label="Caption border color" value={borderColor} onChange={(e) => setBorderColor(e.target.value)} className="absolute inset-0 opacity-0 cursor-pointer" />
                                </label>
                                <div className="flex-1">
                                    <input
                                        aria-label="Caption border width"
                                        type="range"
                                        min="0"
                                        max="5"
                                        value={borderWidth}
                                        onChange={(e) => setBorderWidth(parseInt(e.target.value))}
                                        className="w-full accent-[var(--color-accent)]"
                                    />
                                    <div className="flex justify-between">
                                        <span className="readout">None</span>
                                        <span className="readout">Thick</span>
                                    </div>
                                </div>
                            </div>
                        </div>

                        {/* Background Box */}
                        <div>
                            <div className="flex items-center justify-between mb-2">
                                <p className="eyebrow">Background</p>
                                <label className="relative inline-flex items-center cursor-pointer">
                                    <input type="checkbox" aria-label="Caption background" checked={bgOpacity > 0} onChange={(e) => setBgOpacity(e.target.checked ? 0.5 : 0)} className="sr-only peer" />
                                    <div className="w-8 h-4 rounded-full bg-paper3 peer-checked:bg-brass transition-colors after:content-[''] after:absolute after:top-0 after:left-0 after:h-4 after:w-4 after:rounded-full after:bg-ink after:transition-all peer-checked:after:translate-x-full"></div>
                                </label>
                            </div>
                            {bgOpacity > 0 && (
                                <div className="space-y-3 animate-fade">
                                    <div className="flex items-center gap-3">
                                        <label className="relative w-8 h-8 rounded-input border border-rule2 cursor-pointer overflow-hidden shrink-0" title="Background color">
                                            <div className="w-full h-full" style={{ backgroundColor: bgColor }} />
                                            <input type="color" aria-label="Caption background color" value={bgColor} onChange={(e) => setBgColor(e.target.value)} className="absolute inset-0 opacity-0 cursor-pointer" />
                                        </label>
                                        <div className="flex-1">
                                            <input
                                                aria-label="Caption background opacity"
                                                type="range"
                                                min="10"
                                                max="100"
                                                value={Math.round(bgOpacity * 100)}
                                                onChange={(e) => setBgOpacity(parseInt(e.target.value) / 100)}
                                                className="w-full accent-[var(--color-accent)]"
                                            />
                                            <div className="flex justify-between">
                                                <span className="readout">Transparent</span>
                                                <span className="readout">{Math.round(bgOpacity * 100)}%</span>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            )}
                        </div>
                        </fieldset>
                    </div>

                    <div className="mt-5 shrink-0 space-y-2">
                        {(() => {
                            const bulkRunning = bulkProgress?.running;
                            return (
                                <>
                                    <div className="flex gap-2">
                                        <button onClick={onClose} className="btn-ghost">
                                            cancel
                                        </button>
                                        <button
                                            onClick={saveCaptions}
                                            disabled={!canApply}
                                            className="btn-primary flex-1"
                                        >
                                            {(isProcessing && !bulkRunning) && <Loader2 size={16} className="animate-spin text-brassink" />}
                                            {(isProcessing && !bulkRunning) ? 'Applying…' : activePreset === 'cc_none' ? 'Remove captions' : 'Apply to this clip'}
                                        </button>
                                    </div>
                                    {onApplyAll && bulkCount > 1 && (
                                        <button
                                            onClick={() => onApplyAll({ ...styleOptions, captions: null })}
                                            disabled={isProcessing}
                                            className="btn-ghost w-full flex items-center justify-center gap-2"
                                        >
                                            {bulkRunning
                                                ? <><Loader2 size={16} className="animate-spin" />applying to all… {bulkProgress.current}/{bulkProgress.total}</>
                                                : `apply this style to all ${bulkCount} clips`}
                                        </button>
                                    )}
                                    {/* Clips ship captioned by default, so the way
                                        out has to be here — otherwise a user who
                                        doesn't want captions is stuck with them. */}
                                    {onRemove && (
                                        <button
                                            onClick={removeCaptions}
                                            disabled={isProcessing}
                                            className="text-xs text-muted underline underline-offset-2 lowercase hover:text-ink2 disabled:opacity-50"
                                        >
                                            remove captions from this clip
                                        </button>
                                    )}
                                </>
                            );
                        })()}
                    </div>
                </div>
            </div>
        </Modal>
    );
}
