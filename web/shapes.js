/* ツールグループ別の設備形状パス(共有)。 */
"use strict";
function shapePath(ctx, shape, x, y, w, h) {
  const cx = x + w / 2, cy = y + h / 2, r = Math.min(w, h) / 2;
  ctx.beginPath();
  switch (shape) {
    case "round":
      ctx.roundRect ? ctx.roundRect(x, y, w, h, 6)
                  : ctx.rect(x, y, w, h);
      break;
    case "circle":
      ctx.ellipse(cx, cy, w / 2, h / 2, 0, 0, 7);
      break;
    case "diamond":
      ctx.moveTo(cx, y); ctx.lineTo(x + w, cy);
      ctx.lineTo(cx, y + h); ctx.lineTo(x, cy);
      ctx.closePath(); break;
    case "hexagon": {
      const a = w / 4;
      ctx.moveTo(x + a, y); ctx.lineTo(x + w - a, y);
      ctx.lineTo(x + w, cy); ctx.lineTo(x + w - a, y + h);
      ctx.lineTo(x + a, y + h); ctx.lineTo(x, cy);
      ctx.closePath(); break;
    }
    case "triangle":
      ctx.moveTo(cx, y); ctx.lineTo(x + w, y + h);
      ctx.lineTo(x, y + h); ctx.closePath(); break;
    case "octagon": {
      const a = w / 4, b = h / 4;
      ctx.moveTo(x + a, y); ctx.lineTo(x + w - a, y);
      ctx.lineTo(x + w, y + b); ctx.lineTo(x + w, y + h - b);
      ctx.lineTo(x + w - a, y + h); ctx.lineTo(x + a, y + h);
      ctx.lineTo(x, y + h - b); ctx.lineTo(x, y + b);
      ctx.closePath(); break;
    }
    case "parallelogram": {
      const a = w / 5;
      ctx.moveTo(x + a, y); ctx.lineTo(x + w, y);
      ctx.lineTo(x + w - a, y + h); ctx.lineTo(x, y + h);
      ctx.closePath(); break;
    }
    default:                       // rect
      ctx.rect(x, y, w, h);
  }
}
