"use strict";
(() => {
  const phases=["question","plan","tool","verify","simulate","reveal"], labels=["问题","计划","MCP","核验","回放","结果"];
  const tools=new Set(["run_simulation","compare_simulations","run_server_simulation","compare_server_simulations"]);
  const metrics={average_wait_time:"平均等待时间",average_response_time:"平均响应时间",p95_response_time:"P95 响应时间",max_queue_length:"队列峰值",simulation_end_time:"全部完成时间"};
  const node=(tag,text,cls)=>{const el=document.createElement(tag);el.textContent=text;if(cls)el.className=cls;return el;};
  function configOf(value){
    const c=value||{duration_seconds:30,phase_durations:[3,3,2,2,14,6],default_mode:"analysis"};
    if(!Number.isFinite(c.duration_seconds)||c.duration_seconds<=0||c.phase_durations?.length!==6||c.phase_durations.some(t=>!Number.isFinite(t)||t<=0)||Math.abs(c.phase_durations.reduce((n,t)=>n+t,0)-c.duration_seconds)>1e-9)throw Error("无效演示配置");
    return c;
  }
  function at(config,p,T){let end=0,phase="reveal";p=Math.max(0,Math.min(config.duration_seconds,p));for(let i=0;i<6;i++){end+=config.phase_durations[i];if(p<end){phase=phases[i];break;}}const start=config.phase_durations.slice(0,4).reduce((n,t)=>n+t,0);return {p,phase,t:T*Math.max(0,Math.min(1,(p-start)/config.phase_durations[4]))};}
  class Controller {
    constructor(stage,adapter,player){
      this.stage=stage;this.adapter=adapter;this.player=player;this.mode="analysis";this.p=0;this.driving=false;this.phase=null;this.config=configOf();
      this.preview=node("section","","presentation-preview");this.preview.id="presentation-preview";this.preview.setAttribute("aria-label","Sim2Clip 实验叙事舞台");
      this.root=node("div","","presentation-stage");this.preview.append(this.root);
      this.root.innerHTML='<div class="p-brand">Sim2Clip<span>把问题变成可复现的实验</span></div><div class="p-source"></div><h2 class="p-question"></h2><div class="p-conditions"></div><div class="p-body"></div><div class="p-footer"><div class="p-caption"></div><div class="p-track"></div><div class="p-progress"><div></div></div><div class="p-time"></div></div>';
      this.root.querySelector(".p-track").append(...labels.map(label=>node("span",label)));
      const label=node("label","观看模式 ");this.select=node("select","");this.select.id="view-mode";this.select.append(new Option("Analysis · 分析","analysis"),new Option("Presentation · 演示","presentation"));label.append(this.select);document.querySelector(".layout-tools").prepend(label);this.select.onchange=()=>this.setMode(this.select.value);
      this.analysis=[...document.querySelector("main").children].filter(el=>el.matches("header,#panels,#vertical-preview,.legend,.model-note,.summary,.server-summary,.timeline-section,.report,footer")).map(el=>{const marker=document.createComment("analysis view");el.before(marker);return {el,marker};});
      document.querySelector(".controls").after(this.preview);
      new ResizeObserver(()=>this.resize()).observe(this.preview);
    }
    resize(){if(this.stage.layout==="vertical"){const scale=this.preview.clientWidth/1080;if(scale){this.root.style.transform=`scale(${scale})`;this.preview.style.height=`${1920*scale}px`;}}else{this.root.style.transform="";this.preview.style.height="";}}
    load(manifest){const r=this.player.get().result;this.config=configOf(manifest?.config||this.config);this.execution=manifest?.presentation_version==="1"&&manifest.result_ref?.result_id===(r.comparison_id||r.result_id)?manifest.execution:null;this.phase=null;this.lastExit=null;}
    clear(){this.p=0;this.phase=null;this.execution=null;this.lastExit=null;this.root.querySelector(".p-body").replaceChildren();this.root.querySelector(".p-question").textContent="等待已验证实验结果";this.root.querySelector(".p-conditions").textContent="";this.root.querySelector(".p-source").textContent="未加载有效结果";}
    reset(){this.p=0;this.phase=null;this.lastExit=null;if(this.mode==="presentation")this.seek(0);}
    setMode(mode){
      if(!["analysis","presentation"].includes(mode))throw Error("未知观看模式");if(mode===this.mode)return;
      const state=this.player.get();this.mode=mode;this.select.value=mode;document.body.classList.toggle("presentation-mode",mode==="presentation");
      if(mode==="presentation"){
        this.analysis.forEach(({el})=>el.remove());const previous=this.lastExit;
        this.p=previous&&previous.t===state.clock&&previous.ids===state.runs.map(r=>r.result_id).join(",")?previous.p:this.fromSimulation(state.clock,true);this.phase=null;this.seek(this.p);
      }else{
        this.lastExit={p:this.p,t:state.clock,ids:state.runs.map(r=>r.result_id).join(",")};
        this.stage.root.querySelector(".stage-groups").replaceChildren(...this.stage.cards);this.root.querySelector(".p-body").replaceChildren();this.phase=null;
        this.analysis.forEach(({el,marker})=>marker.after(el));this.stage.setLayout(this.stage.layout);
        this.player.seek(state.clock);document.getElementById("scrubber").setAttribute("aria-label","模拟时间进度");document.querySelector(".clock small").textContent="共享模拟时钟";
        document.getElementById("end-time").textContent=`${state.endTime.toFixed(4)} 秒 · 全部完成`;
        document.querySelector(".scale span").textContent="0 秒 · 模型时钟起点";
      }
      // Rendering an endpoint must not change the playing flag on a mode switch.
      this.player.restorePlaying(state.playing);this.layoutChanged();
    }
    fromSimulation(t,enter=false){const T=this.player.get().endTime,start=this.config.phase_durations.slice(0,4).reduce((n,x)=>n+x,0);return enter&&t===0?0:start+(T?Math.max(0,Math.min(1,t/T)):1)*this.config.phase_durations[4];}
    seekSimulation(t){if(!Number.isFinite(t)||t<0)throw Error("无效模拟时间");return this.seek(this.fromSimulation(t));}
    seek(p){if(!Number.isFinite(p))throw Error("无效展示时间");const state=at(this.config,p,this.player.get().endTime);this.p=state.p;this.driving=true;let snapshots;try{snapshots=this.player.seek(state.t);}finally{this.driving=false;}if(this.mode==="presentation")this.render(state);return snapshots;}
    advance(dt){this.seek(this.p+dt);if(this.p>=this.config.duration_seconds)this.player.pause();}
    layoutChanged(){this.resize();if(this.mode==="presentation")this.render(at(this.config,this.p,this.player.get().endTime),true);}
    state(){const s=this.player.get(),a=at(this.config,this.p,s.endTime);return {mode:this.mode,layout:this.stage.layout,presentation_time:this.p,simulation_time:s.clock,phase:a.phase,playing:s.playing,selected_run_ids:s.runs.map(r=>r.result_id),reveal_visible:this.mode==="presentation"&&a.phase==="reveal"&&this.valid()};}
    valid(){return !(this.execution?.verification||[]).some(v=>v.origin==="agent"&&(v.valid===false||v.sufficient===false));}
    facts(){return this.adapter.presentationFacts(this.player.get().runs,this.player.get().result,this.execution?.primary_metric);}
    render(state,force=false){
      const {runs,result}=this.player.get();if(!runs.length){this.root.querySelector(".p-body").replaceChildren(node("p","等待已验证实验结果"));return;}
      const facts=this.facts(),execution=this.execution||{},body=this.root.querySelector(".p-body");
      this.root.querySelector(".p-question").textContent=execution.question_source==="explicit_public"&&execution.public_question&&this.adapter.publicQuestionApplies(runs,result)?execution.public_question:facts.question;
      this.root.querySelector(".p-conditions").textContent=facts.conditions;
      this.root.querySelector(".p-source").textContent=execution.evidence_completeness==="result_only"||!execution.evidence_completeness?"已保存实验 · 仅有实验结果":"已保存实验 · 执行记录回放";
      this.root.dataset.phase=state.phase;
      if(this.phase!==state.phase||force){
        body.replaceChildren();this.phase=state.phase;
        const card=(title,lines)=>{const el=node("article","","p-info");el.append(node("p",`${String(phases.indexOf(state.phase)+1).padStart(2,"0")} / ${labels[phases.indexOf(state.phase)]}`,"p-kicker"),node("h3",title));for(const line of lines)el.append(node("p",line,"p-line"));body.append(el);return el;};
        if(state.phase==="question")card("一个问题，一场可复现实验",[facts.scene,facts.conditions,facts.assumptions]);
        if(state.phase==="plan"){
          const kind=execution.planner_kind;card(kind==="real_llm"?"AI 已制定实验计划":kind==="offline_demo"?"离线 DemoPlanner 实验计划":"已验证的实验配置",[facts.goal,`控制：${facts.controls}`,`变化：${facts.variables}`,`${result.seeds?"配对 Seed":"Seed"} ${result.seeds?.join("、")||runs[0].config.seed} · 主指标：${metrics[facts.primary]}`]);
        }
        if(state.phase==="tool"){
          const calls=(execution.calls||[]).filter(c=>tools.has(c.name));if(!calls.length)card("仅有实验结果",["未提供 MCP 执行记录","不能确认工具调用历史"]);
          else {const el=card("MCP 执行记录",[]);if(calls.length>2)el.append(node("p",`共 ${calls.length} 次调用 · 展示最后 2 次`,"p-note"));for(const c of calls.slice(-2)){const row=node("div","","p-tool-row");row.append(node("p",c.name,"p-tool-name"),node("p",`${c.status==="success"?"工具调用成功":c.status==="error"?"工具调用失败":"工具状态未记录"} · ${c.cached===true?"已复用缓存":c.cached===false?"未使用缓存":"缓存状态未记录"}`,"p-line"),node("p",c.association==="verified"?"已核对当前结果关联":"关联尚未核实","p-note"));el.append(row);}el.append(node("p","已完成记录回放","p-note"));}
        }
        if(state.phase==="verify"){
          const agent=(execution.verification||[]).find(v=>v.origin==="agent"),exp=(execution.verification||[]).find(v=>v.origin==="export");
          card("独立核验结果",[agent?`Agent 当时验证：${agent.valid===true?"通过":agent.valid===false?"失败":"状态缺失"}${agent.sufficient===true?" · 满足当时证据要求":agent.sufficient===false?" · 证据不足":""}`:"Agent 当时验证：记录缺失",exp?.valid===true?"导出时复核：通过":"本次加载复核：通过",facts.checks,"有限模型的一致性核验，不代表统计显著性"]);
        }
        if(state.phase==="simulate"){
          const groups=node("div","","p-scenes stage-groups");groups.classList.toggle("stage-single",runs.length===1);groups.append(...this.stage.cards);body.append(groups);this.stage.cards.forEach((c,i)=>c.querySelector(".stage-config").after(this.stage.canvases[i]));
          body.append(node("div","","p-live-clock"));
        }
        if(state.phase==="reveal"){
          if(!this.valid())card("验证未通过",["当前 Agent 记录显示验证失败，暂停正常结果揭晓","可在 Analysis 中复核完整证据"]);
          else this.reveal(body,facts,runs,result);
        }
      }
      const captions={question:"问题 → 可验证的实验",plan:"相同输入，真实配置，明确评价指标",tool:"回放保存的执行事实",verify:"执行记录与重新复核分别标注",simulate:runs.length>1?"同一时刻，观察两种方案":"观察这一次真实实验",reveal:"本次模型条件下的观察"};
      this.root.querySelector(".p-caption").textContent=captions[state.phase];this.root.querySelectorAll(".p-track span").forEach((el,i)=>el.classList.toggle("active",phases[i]===state.phase));this.root.querySelector(".p-progress div").style.width=`${state.p/this.config.duration_seconds*100}%`;
      this.root.querySelector(".p-time").textContent=`展示时钟 p · ${state.p.toFixed(1)} / ${this.config.duration_seconds} 秒`;
      if(state.phase==="simulate"){body.querySelector(".p-live-clock").textContent=`共享模拟时钟 t · ${this.adapter.clock(state.t)}`;this.stage.cards.forEach((c,i)=>this.adapter.draw(this.stage.canvases[i],runs[i],this.stage.poses[i],this.stage.prepared[i],this.stage.last.decisions[i]));}
      document.querySelector(".clock small").textContent="展示时钟 p / 秒";document.getElementById("clock").textContent=state.p.toFixed(1);document.getElementById("scrubber").value=Math.round(state.p/this.config.duration_seconds*1000);document.getElementById("scrubber").setAttribute("aria-label","展示时间进度");document.getElementById("end-time").textContent=`${this.config.duration_seconds} 秒 · 演示结束`;document.querySelector(".scale span").textContent="0 秒 · 问题";
      // Bound explicitly allowed long questions without clipping; font size depends only on content.
      const q=this.root.querySelector(".p-question");q.style.fontSize="";if(this.stage.layout==="vertical")for(let size=60;size>34&&q.scrollHeight>q.clientHeight;size-=2)q.style.fontSize=`${size-2}px`;
    }
    reveal(body,facts,runs,result){
      body.append(node("p",`本次回放 · Seed ${runs[0].config.seed}`,"p-scope"));const cards=node("div","","p-results");
      const keys=[facts.primary,...facts.secondary.filter(k=>k!==facts.primary)].slice(0,3);
      for(let i=0;i<runs.length;i++){const r=runs[i],card=node("article","","p-result");card.append(node("h3",`${runs.length===1?"单组":i?"B":"A"} · ${(this.adapter.resultName||this.adapter.name)(r)}`));for(const [j,key] of keys.entries()){const item=node("div","",j?"p-secondary":"p-primary");item.append(node("small",metrics[key]),node("strong",this.adapter.metricText(r,key)));card.append(item);}cards.append(card);}body.append(cards);
      if(runs.length>1){const a=runs[0].metrics[facts.primary],b=runs[1].metrics[facts.primary],delta=b-a,tie=Math.abs(delta)<=Math.max(1e-9,Math.max(Math.abs(a),Math.abs(b))*1e-9);const text=tie?`B − A = ${this.adapter.deltaText(delta,facts.primary)} · 容差内持平`:`B − A = ${this.adapter.deltaText(delta,facts.primary)} · B ${delta<0?"更低":"更高"}`;body.append(node("p",text,"p-delta"));body.append(node("p",a===0?"相对 A 的变化：不可计算（A = 0）":`相对 A：${delta<0?"降低":"增加"} ${Math.abs(delta/a*100).toFixed(2)}% · 分母为 A`,"p-note"));}
      body.append(node("p",result.seeds?`多个配对 Seed 的统计汇总（共 ${result.seeds.length} 个）可在 Analysis 复核` :"单次实验结果 · 未构造对照组或多 Seed 汇总","p-note"));
    }
  }
  window.Sim2ClipPresentation={Controller,at,configOf};
})();
