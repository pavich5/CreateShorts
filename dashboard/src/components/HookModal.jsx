import React, { useState, useEffect } from 'react';
import { Loader2 } from 'lucide-react';
import RemotionPreview from './RemotionPreview';
import Modal from './ui/Modal';
import SegmentedControl from './ui/SegmentedControl';
import { useEditorDraft } from '../lib/useEditorDraft';
import { hookEditorSettings } from '../lib/hookSettings';

const ENTRANCE_OPTIONS = [
    { value: 'spring', label: 'Bounce' },
    { value: 'fade', label: 'Fade' },
    { value: 'slide-up', label: 'Slide Up' },
    { value: 'none', label: 'None' },
];

// Must mirror hooks.py HOOK_STYLES.
const HOOK_STYLES = [
    { value: 'pill', label: 'Pills', box: 'rgba(255,255,255,0.98)', text: '#000', sans: true },
    { value: 'classic', label: 'Classic', box: 'rgba(255,255,255,0.94)', text: '#000' },
    { value: 'dark', label: 'Dark', box: 'rgba(18,18,20,0.92)', text: '#fff' },
    { value: 'yellow', label: 'Yellow', box: 'rgba(255,214,0,0.96)', text: '#000' },
    { value: 'red', label: 'Red', box: 'rgba(220,38,38,0.96)', text: '#fff' },
    { value: 'outline', label: 'Outline', box: 'transparent', text: '#fff', outline: true },
    { value: 'outline_yellow', label: 'Outline+', box: 'transparent', text: '#FFD600', outline: true },
];

// Must mirror hooks.py HOOK_FONTS (all bundled: /fonts/*.ttf).
const FONT_OPTIONS = [
    { value: 'montserrat', label: 'Montserrat' },
    { value: 'anton', label: 'Anton' },
    { value: 'serif', label: 'Serif' },
];
const FONT_CSS = {
    montserrat: { fontFamily: "'Montserrat-ExtraBold', 'Montserrat', sans-serif", fontWeight: 800 },
    anton: { fontFamily: "'Anton-Regular', Impact, sans-serif", fontWeight: 400 },
    serif: { fontFamily: "'Noto Serif', Georgia, serif", fontWeight: 700 },
};
// The style's own typeface when none is picked (what hooks.py renders).
const styleFont = (st) => (st === 'pill' ? 'montserrat' : 'serif');

// auto: the server keeps the hook off the speaker's face and the captions
// (hook_placement.py). The preview cannot see faces and draws it at the top.
const POSITION_OPTIONS = [
    { value: 'auto', label: 'auto' },
    { value: 'top', label: 'top' },
    { value: 'center', label: 'center' },
    { value: 'bottom', label: 'bottom' },
];

const SIZE_OPTIONS = [
    { value: 'S', label: 'Small' },
    { value: 'M', label: 'Medium' },
    { value: 'L', label: 'Large' },
];

// Last-used hook settings, restored on the next open (style always reset to
// classic otherwise, which users read as the picker being broken).
function loadHookPrefs() {
    try { return JSON.parse(localStorage.getItem('os_hook_prefs')) || {}; } catch { return {}; }
}

export default function HookModal({ isOpen, onClose, onGenerate, onRemove, isProcessing, videoUrl, initialText, initialHook = null, durationInSeconds, existingSubtitles, hasCaptions, serverRender, burnedHook, embedded = false, active = true, onEditorStateChange }) {
    const initial = hookEditorSettings(initialHook, initialText || '', loadHookPrefs(), durationInSeconds, serverRender);
    const [text, setText] = useState(initial.text);
    // Stored as `pos`, not `position`: before 'auto' existed every save wrote
    // 'top' whether or not the user ever touched the control.
    const [position, setPosition] = useState(initial.position);
    const [size, setSize] = useState(initial.size);
    const [style, setStyle] = useState(initial.style);
    // null = follow the style's own typeface.
    const [font, setFont] = useState(initial.font);
    const effectiveFont = font || styleFont(style);
    const [entranceAnimation, setEntranceAnimation] = useState(initial.entranceAnimation);
    const [displayDuration, setDisplayDuration] = useState(initial.displayDuration);

    useEffect(() => {
        if (!isOpen) return;
        const saved = hookEditorSettings(initialHook, initialText || '', loadHookPrefs(), durationInSeconds, serverRender);
        setText(saved.text); setPosition(saved.position); setSize(saved.size); setStyle(saved.style);
        setFont(saved.font); setEntranceAnimation(saved.entranceAnimation); setDisplayDuration(saved.displayDuration);
    }, [isOpen, initialHook, initialText, durationInSeconds, serverRender]);
    const maxHookDuration = Math.max(.05, Number(durationInSeconds) || 30);
    const hasSavedGeometry = initialHook && Number.isFinite(initialHook.y)
        && text === initialHook.text && position === (initialHook.position || 'auto')
        && size === (initialHook.size || 'M') && style === (initialHook.style || 'pill')
        && effectiveFont === (initialHook.font || styleFont(initialHook.style || 'pill'));

    // Build hook config for Remotion preview
    const hookConfig = {
        text: text || 'Enter your text...',
        position,
        size,
        style,
        font: effectiveFont,
        entranceAnimation,
        exitFade: !serverRender,
        displayDurationSec: displayDuration,
        ...(hasSavedGeometry ? { y: initialHook.y, scale: initialHook.scale, height: initialHook.height } : {}),
    };
    const draft = { text, position, size, style, font: effectiveFont, entranceAnimation, displayDuration };
    const saveHook = async () => {
        if (isProcessing || !text.trim()) return false;
        try {
            localStorage.setItem('os_hook_prefs', JSON.stringify({ style, font, pos: position, size, entranceAnimation }));
        } catch { /* preferences are optional */ }
        if (await onGenerate({ text, position, size, style, font: effectiveFont, remotion: hookConfig }) !== true) return false;
        markSaved();
        return true;
    };
    const { markSaved } = useEditorDraft({ value: draft, ready: isOpen, canSave: !isProcessing && Boolean(text.trim()), save: saveHook, onStateChange: onEditorStateChange });
    const removeHook = async () => {
        if (isProcessing || !onRemove || await onRemove() !== true) return;
        setText('');
        markSaved({ ...draft, text: '' });
    };

    if (!isOpen) return null;

    const useRemotionPreview = !!videoUrl;

    // Fallback preview logic (same as original)
    const getPositionClass = () => {
        switch (position) {
            case 'center': return 'items-center justify-center';
            case 'bottom': return 'items-center justify-end pb-[20%]';
            case 'top': default: return 'items-center justify-start pt-[20%]';
        }
    };

    const getSizeStyle = () => {
        switch (size) {
            case 'S': return { fontSize: '14px', maxWidth: '80%' };
            case 'L': return { fontSize: '24px', maxWidth: '95%' };
            case 'M': default: return { fontSize: '18px', maxWidth: '90%' };
        }
    };

    const pillFontFace = "@font-face{font-family:'Montserrat-ExtraBold';src:url('/fonts/Montserrat-ExtraBold.ttf') format('truetype');font-weight:800;}"
        + "@font-face{font-family:'Anton-Regular';src:url('/fonts/Anton-Regular.ttf') format('truetype');font-weight:400;}";

    return (
        <Modal isOpen={isOpen} embedded={embedded} onClose={onClose} size="xl" eyebrow="EDIT YOUR SHORT" title="Edit hook" footer={<div className="flex justify-end gap-3">
                        <button onClick={onClose} className="btn-quiet px-4 py-2.5">
                            Cancel
                        </button>
                        <button
                            onClick={saveHook}
                            disabled={isProcessing || !text.trim()}
                            className="btn-primary px-6 py-2.5"
                        >
                            {isProcessing && <Loader2 size={16} className="animate-spin text-brassink" />}
                            {isProcessing ? 'Saving…' : (burnedHook ? 'Update hook' : 'Add hook')}
                        </button>
                    </div>}>
            <style>{pillFontFace}</style>
            <div className="local-hook-editor">
                {/* Left: Preview */}
                <div className="local-hook-preview">
                    {active && (useRemotionPreview ? (
                        <RemotionPreview
                            videoUrl={videoUrl}
                            durationInSeconds={durationInSeconds || 30}
                            hook={hookConfig}
                            subtitles={existingSubtitles || null}
                        />
                    ) : (
                        <>
                            <video src={videoUrl} className="w-full h-full object-contain opacity-50" muted playsInline />
                            <div className={`absolute w-full px-8 text-center transition-all duration-300 pointer-events-none flex flex-col h-full ${getPositionClass()}`}>
                                <div
                                    className="text-black font-bold px-3 py-2 rounded-xl shadow-2xl text-center whitespace-pre-wrap transition-all duration-200"
                                    style={{
                                        ...getSizeStyle(),
                                        backgroundColor: 'rgba(255, 255, 255, 0.82)',
                                        ...FONT_CSS[effectiveFont],
                                        boxShadow: '0 4px 15px rgba(0,0,0,0.5)',
                                        paddingTop: '10px',
                                        paddingBottom: '10px',
                                        paddingLeft: '12px',
                                        paddingRight: '12px'
                                    }}
                                >
                                    {text || "Enter your text..."}
                                </div>
                            </div>
                        </>
                    ))}
                </div>

                {/* Right: Controls */}
                <div className="local-hook-controls min-w-0 flex flex-col">
                    <div className="space-y-5">
                        {/* Text Input */}
                        <div>
                            <div className="flex items-center justify-between gap-3 mb-2"><label htmlFor="hook-text" className="text-sm font-semibold text-ink">Hook text</label><span className="text-xs text-muted">{text.length} characters</span></div>
                            <textarea
                                id="hook-text"
                                value={text}
                                onChange={(e) => setText(e.target.value)}
                                rows={3}
                                className="input-field resize-none"
                                placeholder="Write a short opening line…"
                            />
                        </div>

                        {/* Style (new) */}
                        <div>
                            <p className="text-sm font-semibold text-ink mb-3">Text appearance</p>
                            <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
                                {HOOK_STYLES.map((s) => (
                                    <button
                                        key={s.value}
                                        onClick={() => setStyle(s.value)}
                                        aria-pressed={style === s.value}
                                        className={`p-2 rounded-xl border text-xs transition-colors
                                            ${style === s.value ? 'border-brass bg-brass/5 ring-1 ring-brass' : 'border-rule hover:border-brass/50'}`}
                                        title={s.label}
                                    >
                                        {s.value === 'pill' ? (
                                            <span className="flex flex-col items-center gap-0.5 py-0.5" style={FONT_CSS[font || styleFont(s.value)]}>
                                                <span className="rounded-md px-1.5 text-[13px] leading-5 bg-white text-black">Aa Bb</span>
                                                <span className="rounded-md px-1.5 text-[13px] leading-5 bg-white text-black">Cc</span>
                                            </span>
                                        ) : (
                                            <span
                                                className="block rounded-md px-1 py-1.5 text-[15px] leading-5"
                                                style={{
                                                    ...FONT_CSS[font || styleFont(s.value)],
                                                    backgroundColor: s.box,
                                                    color: s.text,
                                                    textShadow: s.outline ? '-1px -1px 0 #000, 1px -1px 0 #000, -1px 1px 0 #000, 1px 1px 0 #000' : 'none',
                                                }}
                                            >Aa</span>
                                        )}
                                        <span className="block mt-2 text-muted font-medium">{s.label}</span>
                                    </button>
                                ))}
                            </div>
                        </div>

                        {/* Typeface */}
                        <div>
                            <p className="eyebrow mb-2">Font</p>
                            <div className="grid grid-cols-3 gap-1.5">
                                {FONT_OPTIONS.map((f) => (
                                    <button
                                        key={f.value}
                                        onClick={() => setFont(f.value)}
                                        aria-pressed={effectiveFont === f.value}
                                        className={`px-1 py-2 rounded-input border text-[15px] text-ink transition-colors
                                            ${effectiveFont === f.value ? 'border-[color:var(--color-accent)]' : 'border-rule2 hover:border-[color:var(--color-accent)]'}`}
                                        style={FONT_CSS[f.value]}
                                    >
                                        {f.label}
                                    </button>
                                ))}
                            </div>
                        </div>

                        {/* Position Control */}
                        <div>
                            <p className="eyebrow mb-2">Position</p>
                            <SegmentedControl
                                options={POSITION_OPTIONS}
                                value={position}
                                onChange={setPosition}
                                size="sm"
                            />
                            {position === 'auto' && (
                                <p className="text-[11px] text-muted mt-1.5 leading-relaxed">
                                    Placed where it covers no face and no captions.
                                    {hasSavedGeometry ? ' The preview uses its saved placement.' : ' The preview shows an estimate at the top until you save.'}
                                </p>
                            )}
                            {position === 'bottom' && hasCaptions && (
                                <p className="text-[11px] text-warn mt-1.5 leading-relaxed">
                                    This clip has captions near the bottom — the hook may
                                    overlap them (and TikTok's UI). Top is the safe zone.
                                </p>
                            )}
                        </div>

                        {/* Size Control */}
                        <div>
                            <p className="eyebrow mb-2">Size</p>
                            <SegmentedControl
                                options={SIZE_OPTIONS}
                                value={size}
                                onChange={setSize}
                                size="sm"
                            />
                        </div>

                        {/* Entrance Animation (new) */}
                        {!serverRender && (
                        <div>
                            <p className="eyebrow mb-2">Entrance</p>
                            <SegmentedControl
                                options={ENTRANCE_OPTIONS}
                                value={entranceAnimation}
                                onChange={setEntranceAnimation}
                                columns={2}
                                size="sm"
                            />
                        </div>
                        )}

                        {/* Display Duration (new) */}
                        <div>
                            <div className="flex items-center justify-between mb-2">
                                <p className="eyebrow">Duration</p>
                                <span className="text-sm font-semibold text-ink">{displayDuration}s</span>
                            </div>
                            <input
                                aria-label="Hook duration"
                                type="range"
                                min={Math.min(.1, maxHookDuration)}
                                max={maxHookDuration}
                                step="0.1"
                                value={displayDuration}
                                onChange={(e) => setDisplayDuration(Number(e.target.value))}
                                className="w-full accent-[var(--color-accent)]"
                            />
                            <div className="flex justify-between">
                                <span className="readout">{Math.min(.1, maxHookDuration)}S</span>
                                <span className="readout">{Number(maxHookDuration.toFixed(2))}S</span>
                            </div>
                        </div>

                        {burnedHook && (
                            <div className="p-3 border border-rule rounded-input text-xs text-muted space-y-2">
                                <p>
                                    Current hook: “{burnedHook}”. Saving replaces it with your updated text.
                                </p>
                                {onRemove && (
                                    <button
                                        onClick={removeHook}
                                        disabled={isProcessing}
                                        className="text-warn underline underline-offset-2 hover:opacity-80 transition-opacity"
                                    >
                                        remove hook from clip
                                    </button>
                                )}
                            </div>
                        )}

                        <div className="p-3 border border-rule rounded-input text-xs text-muted">
                            Keep the hook short and easy to read. Your changes appear in the preview.
                        </div>
                    </div>


                </div>
            </div>
        </Modal>
    );
}
