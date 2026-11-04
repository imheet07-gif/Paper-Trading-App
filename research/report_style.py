"""Page style and script for the HTML reports research writes (moved from the removed
dashboard/build.py). No external scripts, fonts or requests."""

CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--base:#c3c2b7;--ring:rgba(11,11,11,.10);--accent:#2a78d6;--gain:#2a78d6;--loss:#e34948;
--good:#006300;--warn-bg:#fff6df;--warn-ink:#6b4a00}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--page:#0d0d0d;
--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;--base:#383835;--ring:rgba(255,255,255,.10);
--accent:#3987e5;--gain:#3987e5;--loss:#e66767;--good:#0ca30c;--warn-bg:#2e2611;--warn-ink:#f2cf7a}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;
--grid:#2c2c2a;--base:#383835;--ring:rgba(255,255,255,.10);--accent:#3987e5;--gain:#3987e5;--loss:#e66767;
--good:#0ca30c;--warn-bg:#2e2611;--warn-ink:#f2cf7a}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 64px}
header{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
h1{font-size:22px;margin:0}h2{font-size:16px;margin:0 0 12px}.muted{color:var(--ink2)}
.card{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:18px;margin-top:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-top:16px}
.kpi{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:16px}
.kpi .k{font-size:13px;color:var(--ink2)}.kpi .v{font-size:28px;font-weight:600;margin-top:2px}
.kpi .s{font-size:13px;color:var(--ink2)}
.hbars{display:grid;gap:10px;margin-bottom:8px}
.hb{display:grid;grid-template-columns:minmax(96px,150px) 1fr minmax(120px,auto);align-items:center;gap:10px}
.hb-l{font-size:13px;color:var(--ink2);text-align:right}.hb-t{height:20px}
.hb-b{height:20px;background:var(--accent);border-radius:0 4px 4px 0}.hb-b:hover{opacity:.85}
.hb-v{font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap}
.vbars{margin:8px 0 16px}.vb-plot{position:relative;height:220px;margin:24px 0;display:flex;gap:8px}
.vb-zero{position:absolute;left:0;right:0;border-top:1px solid var(--base)}
.vb-c{position:relative;flex:1;min-width:0}
.vb-b{position:absolute;left:50%;transform:translateX(-50%);width:min(56px,60%)}
.vb-b.gain{background:var(--gain);border-radius:4px 4px 0 0}.vb-b.loss{background:var(--loss);border-radius:0 0 4px 4px}
.vb-c:hover .vb-b{opacity:.85}
.vb-n{position:absolute;left:0;right:0;text-align:center;font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap}
.vb-x{display:flex;gap:8px}.vb-x div{flex:1;min-width:0;text-align:center;font-size:12px;color:var(--muted)}
@media (max-width:560px){.hb{grid-template-columns:1fr auto}.hb-l{grid-column:1/-1;text-align:left}
.hb-t{grid-column:1}.vb-n{font-size:11px}.vb-x div{font-size:11px}}
.tablewrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:8px 10px;border-bottom:1px solid var(--grid);text-align:left;white-space:nowrap}
th{font-weight:600;color:var(--ink2);font-size:13px}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.up{color:var(--good)}.down{color:var(--loss)}
.flag{display:inline-block;font-size:12px;padding:1px 8px;border-radius:999px;background:var(--warn-bg);
color:var(--warn-ink);margin-left:6px}
ul.checks{margin:0;padding-left:20px}ul.checks li{margin:6px 0}
.tag{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;margin-right:6px}
button{font:inherit;font-size:13px;background:var(--surface);color:var(--ink);border:1px solid var(--ring);
border-radius:8px;padding:6px 10px;cursor:pointer}
#tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--surface);font-size:13px;
padding:6px 10px;border-radius:8px;max-width:320px;display:none;z-index:9}
footer{margin-top:24px;font-size:13px;color:var(--ink2)}
"""

JS = """
(function(){var t=document.getElementById('tip');
document.querySelectorAll('[data-tip]').forEach(function(el){
el.addEventListener('mousemove',function(ev){t.textContent=el.getAttribute('data-tip');t.style.display='block';
var x=Math.min(ev.clientX+14,window.innerWidth-t.offsetWidth-8);t.style.left=x+'px';t.style.top=(ev.clientY+14)+'px';});
el.addEventListener('mouseleave',function(){t.style.display='none';});});
var b=document.getElementById('theme');b.addEventListener('click',function(){var r=document.documentElement;
var dark=r.getAttribute('data-theme')?r.getAttribute('data-theme')==='dark':matchMedia('(prefers-color-scheme: dark)').matches;
r.setAttribute('data-theme',dark?'light':'dark');try{localStorage.setItem('theme',r.getAttribute('data-theme'))}catch(e){}});
try{var s=localStorage.getItem('theme');if(s)document.documentElement.setAttribute('data-theme',s)}catch(e){}})();
"""


