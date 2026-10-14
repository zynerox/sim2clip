"use strict";
(() => {
  const colors={ink:"#173b36",muted:"#677972",green:"#117763",amber:"#e99b34",blue:"#478bb4",line:"#dfe5dd"};
  function routePosition(p,y,x){const points=[[48,85],[165,85],[230,85],[230,y],[x,y]],lengths=points.slice(1).map((b,i)=>Math.abs(b[0]-points[i][0])+Math.abs(b[1]-points[i][1]));let distance=lengths.reduce((a,b)=>a+b,0)*p;
    for(let i=0;i<lengths.length;i++){const n=lengths[i];if(n&&distance<=n){const f=distance/n;return [points[i][0]+(points[i+1][0]-points[i][0])*f,points[i][1]+(points[i+1][1]-points[i][1])*f];}distance-=n;}return points.at(-1);}
  class ServerSceneTimeline {
    constructor(run){this.run=run;this.replay=Sim2ClipServerReplay.createReplay(run,true);this.records=new Map(run.request_records.map(r=>[r.request_id,r]));this.height=160+86*run.config.servers.length;this.arrivals=run.request_samples.map(s=>s.arrival_time);}
    seek(t,snapshot){if(!Number.isFinite(t)||t<0)throw Error("无效场景时间");const s=snapshot||this.replay.seek(t,false);if(s.timestamp!==t)throw Error("场景与回放时钟不一致");const entities=[],servers=[];
      this.run.config.servers.forEach((server,index)=>{const id=server.server_id,y=172+86*index,q=s.queues[id],active=s.active[id],count=s.queue_counts?.[id]??q.length;let progress=0;
        if(active!==null){const r=this.records.get(active);progress=Math.min(1,(t-r.processing_start_time)/r.processing_time);}
        servers.push({server_id:id,rate:server.processing_rate,y,queue_count:count,queue_visible:Math.min(6,count),queue_hidden:Math.max(0,count-6),active_request:active,completed:s.completed_per_server[id],outstanding:count+(active!==null?1:0),progress});
        const visible=q.slice(0,6).map((id,rank)=>[id,"waiting",422-rank*23]);if(active!==null)visible.push([active,"processing",515]);
        visible.forEach(([rid,status,target])=>{const r=this.records.get(rid),boundary=r.wait_time>0?r.processing_start_time:r.processing_end_time,duration=Math.min(.35,(boundary-r.arrival_time)*.45);let f=duration>0?Math.min(1,(t-r.arrival_time)/duration):1;f=f*f*(3-2*f);let [x,ypos]=routePosition(f,y,target);if(status==="processing"&&r.wait_time>0){const duration=Math.min(.35,r.processing_time*.45);f=Math.min(1,(t-r.processing_start_time)/duration);f=f*f*(3-2*f);x=422+(target-422)*f;ypos=y;}entities.push({request_id:rid,server_id:id,status,x,y:ypos,moving:f<1,progress:status==="processing"?progress:0});});
      });
      const visible=new Set(entities.map(e=>e.request_id));let lo=0,hi=this.arrivals.length;
      while(lo<hi){const mid=(lo+hi)>>>1;if(this.arrivals[mid]<=t)lo=mid+1;else hi=mid;}
      for(const sample of this.run.request_samples.slice(Math.max(0,lo-3),lo)){
        const r=this.records.get(sample.request_id),duration=Math.min(.35,r.wait_time*.45);
        if(visible.has(sample.request_id)||!(duration>0&&t-sample.arrival_time>=0&&t-sample.arrival_time<duration))continue;
        const server=servers.find(s=>s.server_id===r.server_id);let f=(t-sample.arrival_time)/duration;
        f=f*f*(3-2*f);const [x,y]=routePosition(f,server.y,286);
        entities.push({request_id:sample.request_id,server_id:r.server_id,status:"waiting",x,y,moving:true,progress:0});
        server.queue_visible++;server.queue_hidden--;
      }
      return {timestamp:t,event_cursor:s.event_cursor,width:720,height:this.height,entities,servers,not_arrived:s.not_arrived,waiting:s.waiting,processing:s.processing,completed:s.completed};
    }
  }
  function draw(canvas,run,pose,decision){
    if(canvas.height!==pose.height)canvas.height=pose.height;
    const ctx=canvas.getContext("2d");ctx.clearRect(0,0,720,pose.height);ctx.fillStyle="#fbfcf7";ctx.fillRect(0,0,720,pose.height);
    function text(str,x,y,size=11,color=colors.muted,align="left"){ctx.fillStyle=color;ctx.font=`${size}px "Segoe UI", "Microsoft YaHei", sans-serif`;ctx.textAlign=align;ctx.fillText(str,x,y);}
    function box(x,y,w,h,fill,stroke=colors.line){ctx.fillStyle=fill;ctx.strokeStyle=stroke;ctx.lineWidth=1;ctx.beginPath();ctx.roundRect(x,y,w,h,8);ctx.fill();ctx.stroke();}
    function path(points,color=colors.line,width=2){ctx.strokeStyle=color;ctx.lineWidth=width;ctx.beginPath();points.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));ctx.stroke();}
    text("INCOMING",48,28,10,colors.green,"center");text("LOAD BALANCER",165,28,10,colors.green,"center");text("FIFO / 每节点独立队列",360,128,11,colors.ink,"center");text("单处理槽",515,128,11,colors.ink,"center");text("完成",652,128,11,colors.ink,"center");
    box(20,60,56,50,"#e7f2ec");text("入口",48,48,11,colors.ink,"center");text(`未到达 ${pose.not_arrived}`,48,132,9,colors.muted,"center");
    path([[76,85],[128,85]],"#98b6a9");box(128,54,74,62,colors.ink,colors.ink);text("LB",165,80,22,"white","center");text(run.config.routing_strategy==="round_robin"?"轮换分流":"最少在途",165,102,10,"#d3e3d8","center");path([[202,85],[230,85],[230,pose.servers.at(-1).y]],"#a7bcb2");
    pose.servers.forEach(s=>{const y=s.y,chosen=decision?.server_id===s.server_id;box(253,y-34,444,68,"white");path([[230,y],[440,y]],chosen?colors.green:"#cbd7cf",chosen?3:2);path([[555,y],[622,y]],"#a7bcb2");for(const x of [249,610]){ctx.fillStyle="#91afa0";ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x-6,y-4);ctx.lineTo(x-6,y+4);ctx.closePath();ctx.fill();}
      text(`S${s.server_id} · ${s.rate.toPrecision(3)} work/s`,263,y-18,11,colors.ink);text(`等待 ${s.queue_count} · 处理中 ${s.active_request===null?0:1} · 在途 ${s.outstanding}`,263,y+24,10);
      if(s.queue_hidden)text(`+${s.queue_hidden} 聚合请求`,365,y-18,10,"#9b661e");
      for(let i=0;i<6;i++){box(414-i*23,y-9,17,18,"#faf8ed");}
      box(473,y-23,84,46,s.active_request!==null?"#e7f0f7":"#f5f7f1",s.active_request!==null?colors.blue:colors.line);
      text(s.active_request===null?"IDLE":`#${s.active_request}`,515,y-4,12,colors.ink,"center");text(s.active_request===null?"空闲":`${(s.progress*100).toFixed(0)}%`,515,y+13,10,colors.blue,"center");
      ctx.fillStyle=colors.blue;ctx.fillRect(479,y+18,72*s.progress,3);
      box(624,y-20,58,40,"#e7f2ec");text(String(s.completed),653,y+1,17,colors.green,"center");text("请求",653,y+14,9,colors.green,"center");
      if(chosen){text("最近路由 →",218,y-9,9,colors.green,"right");}
    });
    const shared=new Map();pose.entities.forEach(e=>{const k=`${e.x.toFixed(2)},${e.y.toFixed(2)}`,old=shared.get(k);if(old)old.push(e);else shared.set(k,[e]);});
    for(const entities of shared.values()){const e=entities[0];if(!e.moving&&e.status==="processing")continue;ctx.fillStyle=e.status==="waiting"?colors.amber:colors.blue;ctx.strokeStyle="white";ctx.lineWidth=2;ctx.beginPath();ctx.roundRect(e.x-9,e.y-10,18,20,4);ctx.fill();ctx.stroke();
      const waiting=entities.filter(x=>x.status==="waiting").length,processing=entities.length-waiting;if(waiting&&processing){ctx.fillStyle=colors.blue;ctx.fillRect(e.x,e.y-8,7,16);text(`${waiting}等 / ${processing}处`,e.x,e.y+23,9,colors.ink,"center");}text(entities.length>1?`×${entities.length}`:String(e.request_id),e.x,e.y+3,9,"white","center");}
    text(`已到达 ${run.config.requests-pose.not_arrived} / ${run.config.requests} · 完成 ${pose.completed} · 图形重叠时标记真实请求数`,24,pose.height-20,10);
  }
  window.Sim2ClipServerScene={ServerSceneTimeline,draw};
})();
