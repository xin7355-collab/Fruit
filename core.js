/*
 * core.js —— 精準務農／精準進貨共用的純演算法（不碰畫面、不讀全域變數）。
 * 兩支 App 各放一份相同內容（根目錄與 merchant/），測試會比對兩份一致，並在 Node 跑單元測試：
 *   node --test tests/core.test.cjs
 * 瀏覽器裡掛在 window.Core；Node 裡 require("./core.js")。
 *
 * 為什麼抽出來：這些函式以前寫在兩支 index.html 裡、沒有任何測試；改採收預判或貴賤判斷時壞了也不知道。
 * （做法參考 mina201199/veggie-price-tw 的 prices-core.js＋prices.test.cjs（MIT）；本檔程式為自行撰寫。）
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.Core = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* ── 基本工具（與舊版 index.html 逐字相同） ── */
  function doyOf(iso){const [Y,M,D]=iso.split("-").map(Number);return Math.floor((Date.UTC(Y,M-1,D)-Date.UTC(Y,0,0))/86400000);}
  function weekOf(iso){return Math.floor(doyOf(iso)/7);}
  function median(a){if(!a.length)return null;const s=[...a].sort((x,y)=>x-y);const m=s.length>>1;return s.length%2?s[m]:(s[m-1]+s[m])/2;}
  function pctile(a,p){if(!a.length)return null;const s=[...a].sort((x,y)=>x-y);return s[Math.min(s.length-1,Math.floor(s.length*p))];}
  function sortedEntries(s){return Object.entries(s||{}).filter(e=>e[1]&&e[1].avg>0).sort((a,b)=>a[0]<b[0]?-1:1);}  /* 濾掉 0/無效價 */
  function mergeSeries(dst,src){if(!src||typeof src!=="object")return;for(const c in src){if(src[c]&&typeof src[c]==="object")dst[c]=Object.assign(dst[c]||{},src[c]);}}
  function addDays(iso,n){const t=Date.parse(iso+"T00:00:00Z")+n*86400000;return new Date(t).toISOString().slice(0,10);}

  /* ── 採收預判：同週前後 2 週（含跨年）中位數與 25/75 百分位；不足 5 筆退回近 90 筆 ──
     與 scripts/price_core.py predict_price 同邏輯（tests/test_parity.py 用真實資料比對兩邊結果）。 */
  function predictPrice(seriesObj,harvestISO){const series=sortedEntries(seriesObj);if(!series.length)return null;
    const hw=weekOf(harvestISO);
    const near=series.filter(e=>{const d=Math.abs(weekOf(e[0])-hw);return d<=2||d>=50;}).map(e=>e[1].avg);
    if(near.length>=5)return {med:median(near),lo:pctile(near,.25),hi:pctile(near,.75),n:near.length,same:true};
    const recent=series.slice(-90).map(e=>e[1].avg);
    if(recent.length>=5)return {med:median(recent),lo:pctile(recent,.25),hi:pctile(recent,.75),n:recent.length,same:false};
    return null;}

  /* ── 農曆（2020–2035，已對節氣校驗；與舊版逐字相同） ── */
  const LUNAR={base:Date.UTC(2020,0,25),y0:2020,info:[
    0x07954,0x06aa0,0x0ad50,0x05b52,0x04b60,0x0a6e6,0x0a4e0,0x0d260,0x0ea65,0x0d530,
    0x05aa0,0x076a3,0x096d0,0x04afb,0x04ad0,0x0a4d0]};
  function _lInfo(y){return LUNAR.info[y-LUNAR.y0];}
  function _leapMonth(y){return _lInfo(y)&0xf;}
  function _leapDays(y){return _leapMonth(y)?((_lInfo(y)&0x10000)?30:29):0;}
  function _monthDays(y,m){return (_lInfo(y)&(0x10000>>m))?30:29;}
  function _yearDays(y){let s=348;for(let i=0x8000;i>0x8;i>>=1)s+=(_lInfo(y)&i)?1:0;return s+_leapDays(y);}
  function lunarMD(iso){const p=String(iso).split("-").map(Number);if(p.length<3)return null;
    let off=Math.round((Date.UTC(p[0],p[1]-1,p[2])-LUNAR.base)/86400000);
    if(off<0||p[0]<LUNAR.y0||p[0]>=LUNAR.y0+LUNAR.info.length)return null;
    let y=LUNAR.y0,t=0;while(y<LUNAR.y0+LUNAR.info.length){t=_yearDays(y);if(off<t)break;off-=t;y++;}
    const leap=_leapMonth(y);let isLeap=false,m=1,dm=0;
    /* 修正舊版 bug：閏月結束後的「正常月份」要先清掉閏月旗標再判斷，否則整個月被誤標成閏月
       （例：2025-08-23～09-21 會顯示「閏7/x」，2025 中元、2020 端午因此找不到） */
    for(m=1;m<13;m++){if(leap>0&&m===leap+1&&!isLeap){isLeap=true;m--;dm=_leapDays(y);}
      else{if(isLeap&&m===leap+1)isLeap=false;dm=_monthDays(y,m);}
      if(off<dm)break;off-=dm;}
    return {y:y,m:m,d:off+1,leap:isLeap};}
  function toLunar(iso){const r=lunarMD(iso);return r?{label:(r.leap?"閏":"")+r.m+"/"+r.d}:null;}

  /* ── 節日：自動產生（以前寫死到 2026-09-25 中秋，之後 K 線就沒有節日標記） ──
     農曆節日用上面的農曆表；清明、冬至用節氣公式：日＝floor(Y×0.2422＋C)−floor((Y−1)/4)，Y＝年份後兩位。 */
  function _termDay(year,C){const Y=year%100;return Math.floor(Y*0.2422+C)-Math.floor((Y-1)/4);}
  const LUNAR_FEST=[[1,1,"春節"],[5,5,"端午"],[7,15,"中元"],[8,15,"中秋"]];
  function festivals(y0,y1){const out=[];
    for(let y=Math.max(y0,LUNAR.y0);y<=Math.min(y1,LUNAR.y0+LUNAR.info.length-1);y++){
      for(let iso=y+"-01-01";iso<=y+"-12-31";iso=addDays(iso,1)){const l=lunarMD(iso);if(!l||l.leap)continue;
        for(const [m,d,name] of LUNAR_FEST)if(l.m===m&&l.d===d)out.push([iso,name]);}
      out.push([y+"-04-"+String(_termDay(y,4.81)).padStart(2,"0"),"清明"]);
      out.push([y+"-12-"+String(_termDay(y,21.94)).padStart(2,"0"),"冬至"]);}
    return out.sort((a,b)=>a[0]<b[0]?-1:a[0]>b[0]?1:0);}

  /* ── 俗名搜尋：農業部批發名跟平常講法不同（搜「高麗菜」以前 0 筆） ──
     只放「我們資料裡真的有的批發名」；前端顯示前還會再確認該市場有交易。 */
  const ALIASES={
    "高麗菜":"甘藍","大白菜":"包心白","山東白菜":"包心白","空心菜":"蕹菜","地瓜葉":"甘薯葉","番薯葉":"甘薯葉",
    "地瓜":"甘薯","番薯":"甘薯","小黃瓜":"花胡瓜","大黃瓜":"胡瓜","青椒":"甜椒","彩椒":"甜椒","四季豆":"敏豆",
    "長豆":"菜豆","豇豆":"菜豆","香菜":"芫荽","蔥":"青蔥","蒜頭":"大蒜","白花椰":"花椰菜","花菜":"花椰菜",
    "青花菜":"青花苔","綠花椰":"青花苔","綠花椰菜":"青花苔","大陸妹":"萵苣菜","A菜":"萵苣菜","萵仔菜":"萵苣菜",
    "嫩莖萵苣":"萵苣莖","菜頭":"蘿蔔","白蘿蔔":"蘿蔔","紅蘿蔔":"胡蘿蔔","芋頭":"芋","山藥":"薯蕷","洋芋":"馬鈴薯",
    "秋葵":"黃秋葵","筊白筍":"茭白筍","美人腿":"茭白筍","荷蘭豆":"豌豆","甜豆":"豌豆","花生":"落花生","芥藍":"芥藍菜",
    "刈菜":"芥菜","牛番茄":"番茄","佛手瓜":"隼人瓜","龍鬚菜":"隼人瓜","蒲瓜":"扁蒲","瓠瓜":"扁蒲","瓠仔":"扁蒲",
    "西洋芹":"芹菜","木耳":"濕木耳","香菇":"濕香菇","金針菇":"金絲菇","涼薯":"豆薯","馬蹄":"荸薺","蕗蕎":"蕎頭",
    "甜菜根":"菾菜","青江菜":"青江白菜","打某菜":"茼蒿","老薑":"薑","嫩薑":"薑","芭樂":"番石榴","火龍果":"紅龍果",
    "柳丁":"甜橙","柳橙":"甜橙","橘子":"椪柑","文旦":"柚子","哈密瓜":"洋香瓜","香瓜":"甜瓜","美濃瓜":"甜瓜",
    "聖女番茄":"小番茄","旺來":"鳳梨","榴蓮":"榴槤","波羅蜜":"波蘿蜜","嘉寶果":"樹葡萄","獼猴桃":"奇異果",
    "牛油果":"酪梨","番荔枝":"釋迦","西番蓮":"百香果","印度棗":"蜜棗","柿":"柿子","水蜜桃":"桃子","梅子":"梅","水梨":"梨"};
  function aliasesOf(name){const out=[];for(const a in ALIASES)if(ALIASES[a]===name)out.push(a);return out;}
  /* 名稱或任一俗名含關鍵字；回傳命中的俗名（批發名本身命中回 ""），都沒中回 null */
  function nameHit(name,q){q=String(q||"").trim().toLowerCase();if(!q)return null;
    if(name.toLowerCase().includes(q))return "";
    for(const a of aliasesOf(name))if(a.toLowerCase().includes(q))return a;return null;}
  function nameStarts(name,q){q=String(q||"").trim().toLowerCase();
    return name.toLowerCase().startsWith(q)||aliasesOf(name).some(a=>a.toLowerCase().startsWith(q));}

  /* ── 開價檢查：對方報價 vs 當日批發上/中/下價 ──
     role="farmer"：販仔來收購（比「批發價扣拍賣手續費」後農民實得）；role="merchant"：供應商報價（比批發價本身）。
     unit："kg" 或 "jin"（1 台斤＝0.6 公斤）。回傳 {perKg, level: good|ok|warn|bad, text}。 */
  function judgeOffer(price,unit,day,role,feeRate){
    const p=Number(price);if(!(p>0)||!day||!(day.avg>0))return null;
    const perKg=unit==="jin"?p/0.6:p;const k=role==="farmer"?(1-(feeRate==null?0.06:feeRate)):1;
    const hi=(day.high||day.avg)*k,mid=(day.mid||day.avg)*k,lo=(day.low||day.avg)*k;
    let level,text;
    if(role==="farmer"){
      if(perKg>=hi){level="good";text="高於批發上價（扣手續費後），價格很好";}
      else if(perKg>=mid){level="ok";text="在批發中價～上價之間，合理";}
      else if(perKg>=lo){level="warn";text="低於批發中價，偏低，可以再談";}
      else{level="bad";text="比批發下價還低，可能被壓價";}
    }else{
      if(perKg<=lo){level="good";text="不高於批發下價，很划算";}
      else if(perKg<=mid){level="ok";text="在批發下價～中價之間，合理";}
      else if(perKg<=hi){level="warn";text="高於批發中價，偏貴";}
      else{level="bad";text="比批發上價還貴，太貴了";}
    }
    return {perKg:Math.round(perKg*10)/10,level:level,text:text,ref:{hi:hi,mid:mid,lo:lo}};}

  /* ── 12 個月價格指數：各月中位數 ÷ 全期中位數（>1＝高價月）。資料不足的月份為 null ── */
  function monthIndex(seriesObj){const es=sortedEntries(seriesObj);const all=median(es.map(e=>e[1].avg));
    const by=Array.from({length:12},()=>[]);for(const [d,v] of es)by[Number(d.slice(5,7))-1].push(v.avg);
    return by.map((a,i)=>({m:i+1,n:a.length,idx:(a.length>=3&&all)?Math.round(median(a)/all*100)/100:null}));}

  /* ── 去年同期：把一年前的序列平移到今年的日期（2/29 併到 2/28），K 線畫虛線對照 ── */
  function lastYearSeries(seriesObj,fromISO,toISO){const out={};
    for(const [d,v] of sortedEntries(seriesObj)){let y=Number(d.slice(0,4))+1,md=d.slice(5);if(md==="02-29")md="02-28";
      const nd=y+"-"+md;if((!fromISO||nd>=fromISO)&&(!toISO||nd<=toISO))out[nd]=v;}
    return out;}

  return {doyOf:doyOf,weekOf:weekOf,median:median,pctile:pctile,sortedEntries:sortedEntries,mergeSeries:mergeSeries,
    addDays:addDays,predictPrice:predictPrice,lunarMD:lunarMD,toLunar:toLunar,festivals:festivals,
    ALIASES:ALIASES,aliasesOf:aliasesOf,nameHit:nameHit,nameStarts:nameStarts,judgeOffer:judgeOffer,
    monthIndex:monthIndex,lastYearSeries:lastYearSeries};
});
