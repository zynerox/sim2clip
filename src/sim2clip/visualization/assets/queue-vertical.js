"use strict";
(() => {
  const base=JSON.parse(document.getElementById("experiment-data").textContent).design;
  function prepare(run,endTime,single){
    const height=single?926:356;
    const design={...base,layout:"vertical",width:876,height,queue_columns:5,queue_rows:single?5:3,
      queue_left:186,queue_top:single?198:164,queue_gap_x:66,queue_gap_y:single?135:70,
      entrance:[64,height-54],queue_gate:[186,height-54],service_aisle_x:518,exit:[854,height-22],
      cashier_x:588,cashier_compact_x:558,cashier_top:single?205:108,cashier_gap:single?225:90,
      person_separation:38,person_height:66};
    return {design,scene:new Sim2ClipScene.SceneTimeline(run,design,endTime)};
  }
  function draw(canvas,run,pose,prepared){
    const d=prepared.design,{ctx,text,box,line}=Sim2ClipVertical.painter(canvas,d.width,d.height);
    box(92,52,758,d.height-62,"#f0f2e9","#dce4d8",18);
    box(142,56,362,d.height-102,"#fcf8ee","#e8dec9",16);
    text("排队区",162,91,30,"#936019", "left",650);
    text("队首",478,91,26,"#936019","right");
    const route=Array.from({length:d.queue_columns*d.queue_rows},(_,i)=>Sim2ClipScene.queuePosition(i,d));
    line(route,"#dfc997",3,[5,6]);
    text("入口",30,d.height-150,32,"#173b36","left",700);
    line([[d.entrance[0],d.entrance[1]],[d.queue_gate[0],d.queue_gate[1]]],"#9bb8a1",5);
    text("→",106,d.height-44,34,"#117763","center");
    // Keep the exit sign beside the wall, outside the departure motion lane.
    text("出",844,d.height-158,28,"#117763","center",650);
    text("口",844,d.height-124,28,"#117763","center",650);
    text("→",834,d.height-18,30,"#117763","center",650);
    line([[518,100],[518,d.height-56],[854,d.height-56]],"#aac6b5",3,[6,6]);
    text(`未到达 ${pose.not_arrived}`,28,32,28);
    const visible=pose.cashiers.filter(s=>s.visible),compact=visible.length>3;
    text("收银台",742,32,30,"#173b36","center",700);
    visible.forEach(s=>{
      const busy=s.customer_id!==null,w=compact?90:186,h=visible.length===1?128:80;
      const x=s.x+26,y=s.y-h+20;
      box(x,y,w,h,busy?"#dcebf1":"#e2e9de",busy?"#bad2df":"#cfdbca");
      text(`#${s.cashier_id}`,x+w/2,y+30,compact?26:32,"#173b36","center",700);
      text(busy?"结账中":"空闲",x+w/2,y+58,compact?22:28,busy?"#306f98":"#677972","center");
      if(h>90)text(busy?`顾客 ${s.customer_id}`:"等待顾客",x+w/2,y+92,28,"#306f98","center",500,w-12);
      box(x+10,y+h-12,w-20,5,"#bed2d5",null,2);
      if(busy&&s.progress>0)box(x+10,y+h-12,(w-20)*s.progress,5,"#478bb4",null,2);
    });
    // The existing SceneTimeline resolves collision aggregation at the current t.
    pose.entities.slice().sort((a,b)=>a.y-b.y||a.customer_id-b.customer_id).forEach(p=>{
      ctx.save();ctx.translate(p.x,p.y);ctx.scale(1.38,1.38);ctx.globalAlpha=p.opacity;
      const color=d.colors[p.status],stride=p.moving?Math.sin(pose.timestamp*.6+p.customer_id)*3:0;
      ctx.fillStyle="rgba(23,59,54,.1)";ctx.beginPath();ctx.ellipse(0,3,12,4,0,0,Math.PI*2);ctx.fill();
      ctx.lineCap="round";ctx.lineWidth=5;ctx.strokeStyle="#35534c";ctx.beginPath();ctx.moveTo(-4,-10);ctx.lineTo(-5-stride,0);ctx.moveTo(4,-10);ctx.lineTo(5+stride,0);ctx.stroke();
      box(-10,-29,20,21,color,null,5);ctx.strokeStyle=color;ctx.lineWidth=4;ctx.beginPath();ctx.moveTo(-10,-25);ctx.lineTo(-13,-15-stride);ctx.moveTo(10,-25);ctx.lineTo(13,-15+stride);ctx.stroke();
      ctx.fillStyle="#edcfae";ctx.beginPath();ctx.arc(0,-37,8,0,Math.PI*2);ctx.fill();
      ctx.fillStyle="#173b36";ctx.beginPath();ctx.ellipse(-1,-42,8,4.5,0,Math.PI,Math.PI*2);ctx.fill();ctx.fillRect(p.heading*3,-38,2,2);
      if(p.variant===1){ctx.strokeStyle="#173b36";ctx.lineWidth=1;ctx.strokeRect(-4,-39,8,3);}
      if(p.variant===2)line([[-7,-28],[7,-10]],"#fff",2);
      if(String(p.customer_id).length<=3)text(String(p.customer_id),0,-15,9,"white","center",650);ctx.restore();
    });
    if(pose.queue_hidden){box(150,d.height-40,352,36,"#f9e8c9",null,6);text(`另有 ${pose.queue_hidden} 人等待`,326,d.height-14,28,"#936019","center",650);}
    const hidden=pose.cashiers.filter(s=>!s.visible),hiddenBusy=hidden.filter(s=>s.customer_id!==null).length;
    if(hidden.length){box(532,d.height-43,322,37,"#e7f2ec",null,6);text(`另 ${hidden.length} 台 · 忙碌 ${hiddenBusy}`,694,d.height-16,26,"#173b36","center");}
  }
  window.Sim2ClipQueueVertical={processingLabel:"结账",prepare,draw,total:r=>r.config.customers,
    publicQuestionApplies:(runs,result)=>!result.request||runs.every((r,i)=>r.config.cashiers===result.request.experiments[i].cashiers),
    presentationFacts:(runs)=>{const a=runs[0].config,b=runs[1]?.config;return {
      question:b?`${a.customers} 位顾客，${a.cashiers} 个和 ${b.cashiers} 个收银台，平均等待差多少？`:`${a.customers} 位顾客，${a.cashiers} 个收银台，平均等待多久？`,
      scene:"便利店顾客排队 · 共享 FIFO",
      conditions:b?`A · ${a.cashiers} 个收银台    B · ${b.cashiers} 个收银台`:`单组 · ${a.cashiers} 个收银台`,
      assumptions:`配置：指数到达均值 ${a.mean_interarrival_time} 秒 · 指数服务均值 ${a.mean_service_time} 秒${runs[0].workload_source==="supplied"?"；回放以已保存样本为准":""}`,
      goal:b?"比较收银能力对平均等待的影响":"观察单次便利店等待时间",
      controls:b?"同一批顾客、到达与服务样本、FIFO":"已保存的顾客、到达与服务样本、FIFO",
      variables:b?`收银台数量 ${a.cashiers} → ${b.cashiers}`:"单组，无对照变化项",
      primary:"average_wait_time",secondary:["max_queue_length","simulation_end_time"],checks:"复核人数与事件、FIFO / 容量、指标与样本"};},
    metricText:(r,k)=>k==="max_queue_length"?`${r.metrics[k]} 人`:`${r.metrics[k]>=1e6?r.metrics[k].toExponential(3):r.metrics[k].toFixed(2)} 秒`,
    deltaText:(d,k)=>k==="max_queue_length"?`${d>0?"+":""}${d} 人`:`${d>0?"+":""}${d.toFixed(2)} 秒`,
    name:r=>`${r.config.cashiers} 个收银台`,config:r=>`${r.config.customers} 位顾客 · 共享 FIFO 队列`,
    describe:runs=>({type:runs.length===1?"便利店 · 单组实验":"便利店 · 收银能力对比",question:runs.length===1?
      `${runs[0].config.customers} 位顾客，\n${runs[0].config.cashiers} 个收银台，队列怎样变化？`:
      `${runs[0].config.customers} 位顾客，\n${runs[0].config.cashiers} 个和 ${runs[1].config.cashiers} 个收银台差多少？`,
      conditions:runs.length===1?"按真实顾客事件回放":`A · ${runs[0].config.cashiers} 个收银台    B · ${runs[1].config.cashiers} 个收银台`}),
    pose:(prepared,t,snapshot)=>{const p=prepared.scene.seek(t,snapshot);return {...p,waiting:p.queue_length,processing:p.serving_visible+p.serving_hidden};},
    aria:(r,p)=>`${r.config.cashiers} 个收银台，未到达 ${p.not_arrived}，等待 ${p.waiting}，结账 ${p.processing}，完成 ${p.completed}。另有 ${p.queue_hidden} 人等待聚合。`,
    clock:t=>t>=360000?`${t.toExponential(3)} 秒`:`${String(Math.floor(t/60)).padStart(2,"0")}:${(t%60).toFixed(1).padStart(4,"0")}`};
})();
