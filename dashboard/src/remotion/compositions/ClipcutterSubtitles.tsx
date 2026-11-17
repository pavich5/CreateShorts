import React, { useMemo } from 'react';
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from 'remotion';
import type { SubtitleConfig } from '../lib/types';
import { clipcutterCaptionEvents } from '../../lib/clipcutterCaptionEvents';
import { getFontStack } from '../lib/fonts';

/** ClipCutter's phrase timing and visible preset effects in the live editor. */
export const ClipcutterSubtitles: React.FC<{ config: SubtitleConfig }> = ({ config }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const events = useMemo(() => clipcutterCaptionEvents(config.captions, config.wordsPerCaption ?? 4),
    [config.captions, config.wordsPerCaption]);
  const time = frame / fps;
  const event = events.find(item => item.start <= time && time < item.end);
  const effect = config.captionEffect?.replace('clipcutter:', '') || 'mozi';
  if (!event || effect === 'none') return null;
  const { style } = config;
  const split = config.layoutRanges?.some(item => item.layout === 'split' && item.start <= time && time < item.end);
  const scale = Math.min(width / 1080, height / 1920);
  const size = style.fontSize * scale;
  const border = style.borderWidth * scale;
  const shadow = (style.shadow || 0) * scale;
  const font = style.fontFamily;
  const bundled = font === 'Arial' ? 'OpenShorts Caption Arial' : font === 'Impact' ? 'OpenShorts Caption Impact' : getFontStack(font);
  const position: React.CSSProperties = split ? { top: '50%', transform: 'translateY(-50%)' }
    : config.position === 'middle' ? { top: '50%', transform: 'translateY(-50%)' }
    : config.position === 'top' ? { top: '18%' } : { bottom: '18.75%' };
  const progress = Math.max(0, Math.min(1, (time - event.start) / (event.end - event.start)));
  const active = event.active ?? (effect === 'negative_keywords' ? event.revealed : null);
  const dimmed = /^#([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(style.fontColor);
  const inactiveColor = effect === 'negative_keywords' && dimmed
    ? `rgba(${parseInt(dimmed[1], 16)},${parseInt(dimmed[2], 16)},${parseInt(dimmed[3], 16)},${165 / 255})` : style.fontColor;
  const outline = border ? [0, 45, 90, 135, 180, 225, 270, 315].map(angle =>
    `${Math.cos(angle * Math.PI / 180) * border}px ${Math.sin(angle * Math.PI / 180) * border}px 0 ${style.borderColor}`).join(',') : '';
  const textShadow = [outline, shadow ? `${shadow}px ${shadow}px 0 #00000080` : ''].filter(Boolean).join(',') || 'none';
  const background = style.bgOpacity > 0 ? `${style.bgColor}${Math.round(style.bgOpacity * 255).toString(16).padStart(2, '0')}` : 'transparent';
  return <AbsoluteFill>
    <style>{`@font-face{font-family:'OpenShorts Caption Arial';src:url('/fonts/Montserrat-ExtraBold.ttf') format('truetype');font-weight:700}
      @font-face{font-family:'OpenShorts Caption Impact';src:url('/fonts/Anton-Regular.ttf') format('truetype');font-weight:700}`}</style>
    <div data-caption-engine="clipcutter" data-caption-effect={effect} style={{ position: 'absolute', left: '8.333333%', right: '8.333333%', textAlign: 'center', ...position }}>
      <div style={{ fontFamily: bundled, fontWeight: 700, fontSize: Array.from(event.text).length <= 24 ? size : Math.max(Math.round(size * .55), Math.floor(size * 24 / Array.from(event.text).length)), lineHeight: 1,
        display: 'inline-block', maxWidth: '100%', backgroundColor: background, padding: style.bgOpacity > 0 ? Math.max(6 * scale, border) : 0,
        color: style.fontColor, textShadow: style.bgOpacity > 0 ? (shadow ? `${shadow}px ${shadow}px 0 #00000080` : 'none') : textShadow, textTransform: style.uppercase ? 'uppercase' : 'none' }}>
        {event.words.map((word, index) => <React.Fragment key={index}>{index > 0 && ' '}<span style={{
          color: index === active && effect !== 'marker_sweep' ? style.highlightColor : inactiveColor,
          display: 'inline-block',
          ...(effect === 'marker_sweep' && index === active ? {
            backgroundColor: style.highlightColor, boxShadow: `0 0 0 ${8 * scale}px ${style.highlightColor}`,
            transform: `scaleX(${.65 + .35 * progress})`, color: style.fontColor,
            textShadow: shadow ? `${shadow}px ${shadow}px 0 #00000080` : 'none',
          } : {}),
        }}>{word}</span></React.Fragment>)}
      </div>
    </div>
  </AbsoluteFill>;
};
