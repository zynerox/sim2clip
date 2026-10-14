"use strict";
(() => {
  const node=(tag,text,cls)=>{const el=document.createElement(tag);el.textContent=text;if(cls)el.className=cls;return el;};
  // Shared composition only. Each adapter owns its scene geometry and exact aggregation.
  class Stage {
    constructor(adapter){
      this.adapter=adapter;this.layout="desktop";this.cards=[];this.canvases=[];this.prepared=[];this.poses=[];this.last=null;
      this.preview=node("section","","vertical-preview");this.preview.id="vertical-preview";
      this.preview.setAttribute("aria-label","1080×1920 竖屏实验舞台");
      this.root=node("div","","vertical-stage");this.root.id="vertical-stage";
      this.root.innerHTML='<div class="stage-brand">Sim2Clip<span>实验，让答案看得见</span></div><div class="stage-type"></div><h2 class="stage-question"></h2><div class="stage-conditions"></div><div class="stage-groups"></div><div class="stage-footer"><div class="stage-clock-row"><div><small>共享模拟时钟</small><div class="stage-clock"></div></div><div><small>实验进度</small><div class="stage-percent"></div></div></div><div class="stage-progress"><div class="stage-progress-fill"></div></div><div class="stage-legend"></div><div class="stage-reserve" data-result-slot="reserved">实验结果 · 完整指标见舞台外分析区</div></div>';
      this.preview.append(this.root);document.getElementById("panels").after(this.preview);
      this.legend=this.root.querySelector(".stage-legend");
      for(const [label,color] of [["等待","#e99b34"],[adapter.processingLabel,"#478bb4"],["完成","#117763"]]){
        const item=node("span","");const dot=node("i","");dot.style.background=color;item.append(dot,document.createTextNode(label));this.legend.append(item);
      }
      document.getElementById("layout").onchange=e=>this.setLayout(e.target.value);
      new ResizeObserver(()=>this.resize()).observe(this.preview);
    }
    resize(){const scale=this.preview.clientWidth/1080;if(scale){this.root.style.transform=`scale(${scale})`;this.preview.style.height=`${1920*scale}px`;}}
    fitTitle(){const question=this.root.querySelector(".stage-question");question.style.fontSize="60px";if(this.layout!=="vertical")return;for(let size=60;size>40&&question.scrollHeight>question.clientHeight;size-=2)question.style.fontSize=`${size-2}px`;}
    setLayout(layout){if(!["desktop","vertical"].includes(layout))throw Error("未知布局");this.layout=layout;this.cards.forEach((card,i)=>{if(layout==="vertical")card.querySelector(".stage-config").after(this.canvases[i]);else this.canvases[i].remove();});document.getElementById("layout").value=layout;document.body.classList.toggle("vertical-layout",layout==="vertical");this.fitTitle();this.resize();this.draw();}
    clear(){this.last=null;this.poses=[];this.cards=[];this.canvases=[];this.prepared=[];this.root.querySelector(".stage-groups").replaceChildren();this.root.querySelector(".stage-question").textContent="等待加载有效实验";this.root.querySelector(".stage-conditions").textContent="";this.root.querySelector(".stage-clock").textContent="—";this.root.querySelector(".stage-percent").textContent="—";this.root.querySelector(".stage-progress-fill").style.width="0%";}
    configure(runs,endTime){
      this.runs=runs;this.root.classList.toggle("stage-single",runs.length===1);
      const info=this.adapter.describe(runs);this.root.querySelector(".stage-type").textContent=info.type;
      const question=this.root.querySelector(".stage-question");question.textContent=info.question;question.style.fontSize="60px";
      this.root.querySelector(".stage-conditions").textContent=info.conditions;
      const groups=this.root.querySelector(".stage-groups");groups.replaceChildren();
      this.canvases=[];
      this.prepared=runs.map(run=>this.adapter.prepare(run,endTime,runs.length===1));
      this.cards=runs.map((run,index)=>{
        const card=node("article","","stage-card");card.innerHTML='<div class="stage-card-head"><div class="stage-card-title"><span class="stage-badge"></span><span class="stage-name"></span></div><span class="stage-status"></span></div><div class="stage-config"></div><canvas class="stage-canvas" role="img"></canvas><div class="stage-stats"></div>';
        card.querySelector(".stage-badge").textContent=runs.length===1?"单组":index?"B":"A";
        card.querySelector(".stage-name").textContent=this.adapter.name(run);
        card.querySelector(".stage-config").textContent=this.adapter.config(run);
        for(const [key,label] of [["waiting","等待"],["processing",this.adapter.processingLabel],["completed","完成"]]){
          const cell=node("div","",`stage-stat ${key}`);cell.append(node("small",label),node("strong","0"));card.querySelector(".stage-stats").append(cell);
        }
        const canvas=card.querySelector("canvas");this.canvases.push(canvas);if(this.layout==="desktop")canvas.remove();
        groups.append(card);return card;
      });
      // Fit generated titles including legal maximum counts; no text truncation.
      this.fitTitle();
    }
    update(snapshots,poses,clock,endTime,decisions=[]){
      this.poses=snapshots.map((snapshot,i)=>this.adapter.pose(this.prepared[i],clock,snapshot,poses[i]));
      this.last={snapshots,clock,endTime,decisions};
      this.cards.forEach((card,i)=>{
        const p=this.poses[i];for(const key of ["waiting","processing","completed"])card.querySelector(`.stage-stat.${key} strong`).textContent=p[key];
        card.querySelector(".stage-status").textContent=p.completed===this.adapter.total(this.runs[i])?"全部完成":"回放中";
        this.canvases[i].setAttribute("aria-label",this.adapter.aria(this.runs[i],p));
      });
      this.root.querySelector(".stage-clock").textContent=this.adapter.clock(clock);
      const percent=endTime?Math.min(100,clock/endTime*100):100;this.root.querySelector(".stage-percent").textContent=`${percent.toFixed(0)}%`;
      this.root.querySelector(".stage-progress-fill").style.width=`${percent}%`;this.draw();
    }
    draw(){if(this.layout!=="vertical"||!this.last)return;this.cards.forEach((card,i)=>this.adapter.draw(this.canvases[i],this.runs[i],this.poses[i],this.prepared[i],this.last.decisions[i]));}
  }
  function painter(canvas,width,height){
    // Stable logical pixels: DOM and canvas can be captured at native 1080×1920.
    if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height;}
    const ctx=canvas.getContext("2d");ctx.clearRect(0,0,width,height);ctx.fillStyle="#fbfcf7";ctx.fillRect(0,0,width,height);
    const text=(str,x,y,size=32,color="#677972",align="left",weight=500,maxWidth=null)=>{ctx.fillStyle=color;ctx.font=`${weight} ${size}px "Segoe UI","Microsoft YaHei",sans-serif`;if(maxWidth&&ctx.measureText(str).width>maxWidth){size*=maxWidth/ctx.measureText(str).width;ctx.font=`${weight} ${size}px "Segoe UI","Microsoft YaHei",sans-serif`;}ctx.textAlign=align;ctx.fillText(str,x,y);};
    const box=(x,y,w,h,fill,stroke="#dfe5dd",radius=12)=>{ctx.beginPath();ctx.roundRect(x,y,w,h,radius);ctx.fillStyle=fill;ctx.fill();if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=2;ctx.stroke();}};
    const line=(points,color="#cbd7cf",width=3,dash=[])=>{ctx.beginPath();points.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));ctx.strokeStyle=color;ctx.lineWidth=width;ctx.setLineDash(dash);ctx.stroke();ctx.setLineDash([]);};
    return {ctx,text,box,line};
  }
  window.Sim2ClipVertical={Stage,painter};
})();
