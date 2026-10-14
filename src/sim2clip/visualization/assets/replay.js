"use strict";
(() => {
  const payload = JSON.parse(document.getElementById("experiment-data").textContent);
  const elements=Object.fromEntries([...document.querySelectorAll('[id]')].map(el=>[el.id,el]));
  const $ = id => elements[id];
  const {createReplay, validateEvidence} = window.Sim2ClipReplay;
  const design = payload.design;
  const stage=new Sim2ClipVertical.Stage(Sim2ClipQueueVertical);
  let result, runs, context, timeline, selected, replays, scenes, panels, endTime=0, clock=0, playing=false, lastFrame=0;
  let narrative;
  function textNode(tag,text,className) {const el=document.createElement(tag);el.textContent=text;if(className)el.className=className;return el;}
  function clockText(t) {return `${String(Math.floor(t/60)).padStart(2,"0")}:${(t%60).toFixed(1).padStart(4,"0")}`;}
  function makePanel(run,index) {
    const panel=document.createElement("article");panel.className=`panel ${index?"b":"a"}`;
    panel.innerHTML='<div class="panel-head"><div class="panel-title"><span class="badge"></span><h3></h3></div><span class="tag run-status"></span></div><div class="params"></div><div class="stats"><div class="stat"><small>当前排队</small><strong class="queue">0</strong><span>人</span></div><div class="stat"><small>已完成</small><strong class="done">0</strong><span>人</span></div><div class="stat"><small>忙碌收银台</small><strong class="busy">0</strong><span class="capacity"></span></div></div><canvas class="store-scene" width="720" height="440" role="img" aria-label="事件驱动的便利店场景"></canvas><div class="scene-accounting"></div><div class="panel-foot"><span>本次完整实验平均等待 <strong class="wait"></strong>秒</span><span class="peak"></span></div>';
    panel.querySelector(".badge").textContent=index?"B":"A";panel.querySelector("h3").textContent=`${run.config.cashiers} 个收银台`;
    panel.querySelector(".params").textContent=`${run.config.customers} 位顾客 · 到达间隔均值 ${run.config.mean_interarrival_time}s · 服务均值 ${run.config.mean_service_time}s · seed ${run.config.seed}`;
    panel.querySelector(".capacity").textContent=`/ ${run.config.cashiers}`;
    panel.querySelector(".wait").textContent=run.metrics.average_wait_time.toFixed(2);panel.querySelector(".peak").textContent=`最长队列 ${run.metrics.max_queue_length} 人`;
    $("panels").append(panel);return panel;
  }
  function choose() {
    pause();const seed=Number($("seed").value), groups=result.request?.experiments.map(c=>c.cashiers)||[result.config.cashiers];
    selected=[runs.find(r=>r.config.seed===seed&&r.config.cashiers===groups[0])];
    if(groups.length>1)selected.push(runs.find(r=>r.config.seed===seed&&r.config.cashiers===Number($("candidate").value)));
    replays=selected.map(createReplay);endTime=Math.max(...selected.map(r=>r.metrics.simulation_end_time));
    $("panels").replaceChildren();$("panels").classList.toggle("single",selected.length===1);panels=selected.map(makePanel);
    scenes=selected.map(r=>new Sim2ClipScene.SceneTimeline(r,design,endTime));
    stage.configure(selected,endTime);
    $("end-time").textContent=`${endTime.toFixed(1)} 秒 · 全部完成`;
    seek(0);
    narrative.reset();
  }
  function seek(t) {
    if(narrative?.mode==="presentation"&&!narrative.driving)return narrative.seekSimulation(t);
    clock=Math.max(0,Math.min(endTime,t));$("clock").textContent=clockText(clock);$("scrubber").value=Math.round(clock/endTime*1000);
    const snapshots=replays.map(r=>r.seek(clock)),poses=[];
    snapshots.forEach((s,i)=> {
      panels[i].querySelector(".queue").textContent=s.queue.length;panels[i].querySelector(".done").textContent=s.completed;
      panels[i].querySelector(".busy").textContent=s.cashiers.filter(c=>c!==null).length;
      panels[i].querySelector(".run-status").textContent=s.completed===selected[i].config.customers?"全部完成":"事件回放中";
      const pose=scenes[i].seek(clock,s), canvas=panels[i].querySelector("canvas");
      poses.push(pose);
      Sim2ClipScene.draw(canvas,selected[i],pose,design);
      const hiddenCounters=pose.cashiers.filter(c=>!c.visible), hiddenBusy=hiddenCounters.filter(c=>c.customer_id!==null).length;
      panels[i].querySelector(".scene-accounting").textContent=`未到达 ${pose.not_arrived} 人 · 排队 ${pose.queue_visible} + 聚合 ${pose.queue_hidden} 人 · 结账 ${pose.serving_visible} + 聚合 ${pose.serving_hidden} 人 · 已完成 ${pose.completed_visible} 离场 + ${pose.completed_hidden} 人`+(hiddenCounters.length?` · 另有 ${hiddenCounters.length} 台收银台（${hiddenBusy} 台忙碌）`:"");
      canvas.setAttribute("aria-label",`便利店：未到达 ${pose.not_arrived} 人，排队 ${pose.queue_length} 人，正在结账 ${pose.serving_visible+pose.serving_hidden} 人，已完成 ${pose.completed} 人。移动路径为示意，状态按真实事件更新。`);
    });
    stage.update(snapshots,poses,clock,endTime);
    if(clock>=endTime&&narrative?.mode!=="presentation")pause();return snapshots;
  }
  function pause() {playing=false;$("play").textContent="播放实验 ▶";}
  function restorePlaying(value){playing=value;lastFrame=performance.now();$("play").textContent=value?"暂停回放 Ⅱ":"播放实验 ▶";}
  function play() {if(narrative.mode==="presentation"){if(narrative.p>=narrative.config.duration_seconds)narrative.seek(0);}else if(clock>=endTime)seek(0);restorePlaying(true);}
  function tick(now) {if(playing){const dt=Math.min((now-lastFrame)/1000,0.25)*Number($("speed").value);if(narrative.mode==="presentation")narrative.advance(dt);else seek(clock+dt*endTime/design.playback_seconds);lastFrame=now;}requestAnimationFrame(tick);}
  function renderTimeline() {
    $("timeline").replaceChildren();
    if(!timeline.length){$("timeline").append(textNode("li","此 JSON 只包含实验结果，没有 Agent 执行记录。"));return;}
    timeline.forEach(item=> {
      const li=document.createElement("li"), row=textNode("div","","row");row.append(textNode("strong",item.name),textNode("span",`${item.status} · R${item.round}`,item.status));li.append(row);
      const time=textNode("time",item.timestamp?new Date(item.timestamp).toLocaleString("zh-CN",{hour12:false}):"时间未记录");time.title=item.timestamp||"原始状态没有墙钟时间";li.append(time,textNode("p",`${item.node} · ${item.action}`),textNode("p",item.reason));
      for(const call of item.tool_calls) {const details=document.createElement("details");details.append(textNode("summary",`${call.name} · attempt ${call.attempt} · ${call.status}`),textNode("pre",JSON.stringify(call.arguments,null,2)));li.append(details);}
      $("timeline").append(li);
    });
  }
  function renderSummary() {
    $("summary-table").replaceChildren();
    const count=result.seeds?.length||1;
    $("summary-note").textContent=result.comparison_id?`汇总 ${count} 个配对种子（${result.seeds.join(", ")}）。上方动画仅显示选中的单次实验。范围是种子间观测范围，不是置信区间。`:"当前加载单次实验。A/B 对比需要完整 ComparisonResult。";
    const table=document.createElement("table");table.innerHTML='<thead><tr><th>收银台</th><th>平均等待（秒）</th><th>最长队列</th></tr></thead><tbody></tbody>';
    const rows=result.summaries||[{cashiers:result.config.cashiers,average_wait_time:{mean:result.metrics.average_wait_time},max_queue_length:{mean:result.metrics.max_queue_length}}];
    rows.forEach(s=> {const tr=document.createElement("tr");tr.append(textNode("td",`${s.cashiers} 个`),textNode("td",s.average_wait_time.mean.toFixed(2)),textNode("td",s.max_queue_length.mean.toFixed(2)));table.querySelector("tbody").append(tr);});
    $("summary-table").append(table);
  }
  function loadEvidence(value,info={}) {
    const checked=validateEvidence(value);pause();result=value;runs=checked;context=info.context||{};timeline=info.agent_timeline||[];
    const groups=result.request?.experiments.map(c=>c.cashiers)||[result.config.cashiers], seeds=result.seeds||[result.config.seed];
    const official=runs[0].config.customers===100&&groups[0]===1&&groups[1]===3&&seeds.includes(42)&&runs[0].config.mean_interarrival_time===10&&runs[0].config.mean_service_time===25;
    $("scenario-label").textContent=`${official?"OFFICIAL DEMO":"EXPERIMENT REPLAY"} / CONVENIENCE STORE RUSH`;
    $("headline").replaceChildren(textNode("span",`同样 ${runs[0].config.customers} 位顾客。`),document.createElement("br"),textNode("span",groups.length>1?`${groups[0]} 个 vs ${groups[1]} 个收银台，会发生什么？`:"回放一次完整的结账实验。"));
    $("goal").textContent=(context.experiment_goal||"同一批顾客，同一模拟时钟。看队列如何变长，又如何被消化。")+(context.selection_note?` ${context.selection_note}`:"");
    $("provenance").textContent=`${context.planner_mode==="offline_demo"?"离线 DemoPlanner · ":""}${result.comparison_id?"comparison":"result"} ${(result.comparison_id||result.result_id).slice(0,12)} · ${runs.length} 次模拟`;
    $("seed").replaceChildren(...seeds.map(s=>{const o=textNode("option",String(s));o.value=s;return o;}));$("seed").value=seeds.includes(42)?42:seeds[0];
    $("candidate").replaceChildren(...groups.slice(1).map(c=>{const o=textNode("option",`${c} 个收银台`);o.value=c;return o;}));$("candidate-label").hidden=groups.length===1;
    $("report").textContent=context.report||"此文件未提供 Agent 报告。指标见上方与原始 JSON。";
    narrative.load(info.presentation);$("error").hidden=true;renderSummary();renderTimeline();choose();
    if(info.presentation?.config.default_mode==="presentation")narrative.setMode("presentation");
  }
  function showError(error) {pause();$("error").textContent=`无法回放：${error.message}。请提供完整、未经修改的结果 JSON。`;$("error").hidden=false;}
  narrative=new Sim2ClipPresentation.Controller(stage,Sim2ClipQueueVertical,{get:()=>({result,runs:selected||[],endTime,clock,playing}),seek,pause,restorePlaying});
  $("play").onclick=()=>playing?pause():play();$("restart").onclick=()=>{pause();if(narrative.mode==="presentation")narrative.seek(0);else seek(0);};
  $("scrubber").oninput=()=>{pause();const ratio=Number($("scrubber").value)/1000;if(narrative.mode==="presentation")narrative.seek(ratio*narrative.config.duration_seconds);else seek(ratio*endTime);};
  $("seed").onchange=choose;$("candidate").onchange=()=>{choose();$("headline").lastChild.textContent=`${selected[0].config.cashiers} 个 vs ${selected[1].config.cashiers} 个收银台，会发生什么？`;};
  $("load-file").onchange=async()=>{try{loadEvidence(JSON.parse(await $("load-file").files[0].text()));}catch(error){showError(error);}finally{$("load-file").value="";}};
  $("download").onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:"application/json"})),a=document.createElement("a");a.href=url;a.download="result.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  const setLayout=layout=>{stage.setLayout(layout);narrative.layoutChanged();};$("layout").onchange=e=>setLayout(e.target.value);
  window.Sim2Clip={createReplay,validateEvidence,createScene:(run,horizon)=>new Sim2ClipScene.SceneTimeline(run,design,horizon),getScenePoses:()=>scenes.map(scene=>scene.seek(clock)),getStagePoses:()=>stage.poses,setLayout,getLayout:()=>stage.layout,setMode:mode=>narrative.setMode(mode),seekPresentation:p=>narrative.seek(p),getPresentationState:()=>narrative.state(),loadEvidence,seek,pause,play,getState:()=>({clock,endTime,playing,selected:selected.map(r=>r.result_id),snapshots:replays.map(r=>r.seek(clock))}),getResult:()=>result};
  try{loadEvidence(payload.result,payload);}catch(error){showError(error);$("play").disabled=true;}
  requestAnimationFrame(tick);
})();
