"use strict";
(() => {
  const queuePosition = (rank, d) => {
    const row=Math.floor(rank/d.queue_columns);
    let col=rank%d.queue_columns;
    if(row%2===0)col=d.queue_columns-1-col;
    return [d.queue_left+col*d.queue_gap_x,d.queue_top+row*d.queue_gap_y];
  };
  function cashierPosition(id,count,d) {
    const visible=Math.min(count,d.visible_cashiers), index=(id-1)%d.visible_cashiers;
    const page=Math.floor((id-1)/d.visible_cashiers);
    if(d.layout==="vertical")return visible<=3?
      [d.cashier_x+page*d.width,d.height/2+26+(index-(visible-1)/2)*d.cashier_gap]:
      [d.cashier_compact_x+Math.floor(index/3)*150+page*d.width,d.cashier_top+index%3*d.cashier_gap];
    return visible<=3 ? [548+page*d.width,225+(index-(visible-1)/2)*92] :
      [500+Math.floor(index/3)*108+page*d.width,133+index%3*92];
  }
  function sample(m,t) {
    const progress=m.duration<=0?1:Math.min(1,Math.max(0,(t-m.timestamp)/m.duration));
    const eased=progress*progress*(3-2*progress);
    if(progress>=1)return [...m.path.at(-1),m.rest_heading,false];
    const lengths=m.path.slice(1).map((p,i)=>Math.hypot(p[0]-m.path[i][0],p[1]-m.path[i][1]));
    let distance=eased*lengths.reduce((a,b)=>a+b,0);
    for(let i=0;i<lengths.length;i++) {
      const length=lengths[i];
      if(distance<=length+1e-9&&length>1e-9) {
        const a=m.path[i],b=m.path[i+1],ratio=Math.min(1,Math.max(0,distance/length));
        return [a[0]+(b[0]-a[0])*ratio,a[1]+(b[1]-a[1])*ratio,b[0]<a[0]?-1:1,progress<1];
      }
      distance-=length;
    }
    return [...m.path.at(-1),1,false];
  }
  class SceneTimeline {
    constructor(run,design,endTime=run.metrics.simulation_end_time) {
      this.run=run;this.design=design;this.endTime=endTime;
      if(!Number.isFinite(endTime)||endTime<run.metrics.simulation_end_time)throw Error("场景时钟必须覆盖全部事件");
      this.replay=Sim2ClipReplay.createReplay(run);
      this.records=new Map(run.customer_records.map(r=>[r.customer_id,r]));
      this.tracks=new Map();this.capacity=design.queue_columns*design.queue_rows;
      this.build();
    }
    motion(id,t) {
      const tracks=this.tracks.get(id)||[];
      let left=0,right=tracks.length;
      while(left<right){const mid=(left+right)>>>1;if(tracks[mid].timestamp<=t)left=mid+1;else right=mid;}
      return tracks[left-1];
    }
    add(id,t,status,rank=null) {
      const d=this.design,r=this.records.get(id),previous=this.motion(id,t);
      let target=rank!==null?queuePosition(rank,d):cashierPosition(r.cashier_id,this.run.config.cashiers,d);
      if(previous&&previous.status===status&&previous.path.at(-1).every((v,i)=>v===target[i]))return;
      const source=previous?sample(previous,t).slice(0,2):r.arrival_time===t?[...d.entrance]:queuePosition(this.capacity-1,d);
      let duration=d.motion_seconds;const path=[source];
      if(status==="waiting") {
        if(previous&&previous.status==="waiting"&&previous.queue_rank!==null) {
          for(let k=previous.queue_rank;k>=rank;k--)path.push(queuePosition(k,d));
        }else{
          if(r.arrival_time===t)path.push([...d.queue_gate]);
          for(let k=this.capacity-1;k>=rank;k--)path.push(queuePosition(k,d));
        }
        duration=Math.min(duration,r.service_start_time-t);
      }else if(status==="serving") {
        path.push([d.service_aisle_x,previous?source[1]:d.queue_gate[1]],[d.service_aisle_x,target[1]],target);
        duration=Math.min(duration,r.service_time*0.45);
      }else{
        const lane=d.exit[1]+(id%3-1)*8;
        target=[d.exit[0],lane];path.push([source[0],lane],target);
        duration=Math.min(duration,this.endTime-t);
      }
      if(path.at(-1).some((v,i)=>v!==target[i]))path.push(target);
      if(!this.tracks.has(id))this.tracks.set(id,[]);
      this.tracks.get(id).push({timestamp:t,duration,path,status,queue_rank:rank,
        rest_heading:rank!==null&&Math.floor(rank/d.queue_columns)%2?-1:1});
    }
    build() {
      const queue=[],active=new Map(),events=this.run.events;
      let head=0,index=0;
      while(index<events.length) {
        const t=events[index].timestamp,finished=[];
        do {
          const e=events[index++];
          if(e.event_type==="customer_arrived")queue.push(e.customer_id);
          else if(e.event_type==="customer_started"){head++;active.set(e.cashier_id,e.customer_id);}
          else {active.delete(e.cashier_id);finished.push(e.customer_id);}
        }while(index<events.length&&events[index].timestamp===t);
        queue.slice(head,head+this.capacity).forEach((id,rank)=>this.add(id,t,"waiting",rank));
        [...active].sort((a,b)=>a[0]-b[0]).forEach(([cashier,id])=>{
          if(cashier<=this.design.visible_cashiers)this.add(id,t,"serving");
        });
        finished.forEach(id=>{if(this.records.get(id).cashier_id<=this.design.visible_cashiers)this.add(id,t,"completed");});
      }
    }
    seek(t,snapshot=this.replay.seek(t)) {
      if(!Number.isFinite(t)||t<0||snapshot.timestamp!==t)throw Error("场景与回放时钟不一致");
      const d=this.design;
      const cashiers=snapshot.cashiers.map((id,index)=>{
        const r=id===null?null:this.records.get(id),[x,y]=cashierPosition(index+1,this.run.config.cashiers,d);
        return {cashier_id:index+1,x,y,customer_id:id,visible:index<d.visible_cashiers,
          progress:r?Math.min(1,Math.max(0,(t-r.service_start_time)/r.service_time)):0};
      });
      const waiting=snapshot.queue.slice(0,this.capacity),serving=cashiers.filter(c=>c.visible&&c.customer_id!==null).map(c=>c.customer_id);
      const departures=[...this.records.values()].filter(r=>r.cashier_id<=d.visible_cashiers&&
        r.service_end_time<=t&&t<r.service_end_time+Math.min(d.motion_seconds,this.endTime-r.service_end_time))
        .sort((a,b)=>b.service_end_time-a.service_end_time||a.customer_id-b.customer_id)
        .slice(0,d.visible_departures).map(r=>r.customer_id);
      const entities=[...waiting,...serving,...departures].map(id=>{
        const m=this.motion(id,t),[x,y,heading,moving]=sample(m,t),status=snapshot.customer_states[id];
        return {customer_id:id,status,x,y,heading,moving,
          opacity:status==="completed"&&m.duration>0?Math.min(1,Math.max(0,(1-(t-m.timestamp)/m.duration)/0.25)):1,
          queue_rank:status==="waiting"?waiting.indexOf(id):null,
          cashier_id:status==="waiting"?null:this.records.get(id).cashier_id,
          target_x:m.path.at(-1)[0],target_y:m.path.at(-1)[1],variant:id%4};
      });
      const priority={serving:0,waiting:1,completed:2,not_arrived:3};
      entities.sort((a,b)=>priority[a.status]-priority[b.status]||(a.queue_rank||0)-(b.queue_rank||0)||a.customer_id-b.customer_id);
      const visibleEntities=[];
      entities.forEach(person=>{
        if(person.opacity>=0.15&&visibleEntities.every(other=>Math.abs(person.x-other.x)>=d.person_separation||Math.abs(person.y-other.y)>=d.person_height))visibleEntities.push(person);
      });
      const shownWaiting=visibleEntities.filter(p=>p.status==="waiting").length,
        shownServing=visibleEntities.filter(p=>p.status==="serving").length,
        shownCompleted=visibleEntities.filter(p=>p.status==="completed").length;
      const busy=cashiers.filter(c=>c.customer_id!==null).length;
      return {timestamp:t,entities:visibleEntities,cashiers,queue_length:snapshot.queue.length,queue_visible:shownWaiting,
        queue_hidden:snapshot.queue.length-shownWaiting,serving_visible:shownServing,serving_hidden:busy-shownServing,
        completed:snapshot.completed,completed_visible:shownCompleted,completed_hidden:snapshot.completed-shownCompleted,
        not_arrived:this.run.config.customers-snapshot.queue.length-busy-snapshot.completed};
    }
  }

  function draw(canvas,run,pose,d) {
    const ratio=Math.min(window.devicePixelRatio||1,2);
    if(canvas.width!==d.width*ratio||canvas.height!==d.height*ratio){canvas.width=d.width*ratio;canvas.height=d.height*ratio;}
    const ctx=canvas.getContext("2d"),c=d.colors;
    ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,d.width,d.height);
    const round=(x,y,w,h,r,fill,stroke)=>{
      ctx.beginPath();ctx.roundRect(x,y,w,h,r);
      if(fill){ctx.fillStyle=fill;ctx.fill();}if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=1;ctx.stroke();}
    };
    const text=(value,x,y,size=13,color=c.muted,weight=400,align="left")=>{
      ctx.font=`${weight} ${size}px "Segoe UI","Microsoft YaHei",sans-serif`;ctx.fillStyle=color;ctx.textAlign=align;ctx.fillText(value,x,y);
    };
    const line=(points,color,width=1,dash=[])=>{
      ctx.beginPath();ctx.moveTo(...points[0]);points.slice(1).forEach(p=>ctx.lineTo(...p));ctx.strokeStyle=color;ctx.lineWidth=width;ctx.setLineDash(dash);ctx.stroke();ctx.setLineDash([]);
    };
    const arrow=(x,y,heading,color)=>{
      ctx.beginPath();ctx.moveTo(x-5*heading,y-4);ctx.lineTo(x,y);ctx.lineTo(x-5*heading,y+4);ctx.strokeStyle=color;ctx.lineWidth=1.5;ctx.stroke();
    };
    ctx.fillStyle="#fbfcf7";ctx.fillRect(0,0,d.width,d.height);
    text("便利店 / 共享 FIFO 队列",24,30,14,c.ink,650);
    text(`未到达 ${pose.not_arrived} 人`,694,30,13,c.muted,400,"right");
    round(24,49,672,351,14,"#f0f2e9","#dce4d8");
    // Architectural floor plan, not decorative customer markers.
    for(let x=48;x<696;x+=48)line([[x,51],[x,398]],"#e7eadf");
    for(let y=78;y<399;y+=48)line([[26,y],[694,y]],"#e7eadf");
    round(114,87,329,254,12,"#fcf8ee","#e8dec9");
    text("等待区",130,109,13,"#8a681d",650);
    text(pose.queue_length?`${pose.queue_length} 人等待` : "队列畅通",426,109,13,pose.queue_length?"#8a681d":c.completed,500,"right");
    const route=Array.from({length:d.queue_columns*d.queue_rows},(_,i)=>queuePosition(i,d));
    line(route,"#e6d5ac",2,[4,5]);
    route.forEach(([x,y])=>{ctx.beginPath();ctx.arc(x,y+5,8,0,Math.PI*2);ctx.strokeStyle="#e7dbc1";ctx.lineWidth=1;ctx.stroke();});
    text("队首",426,78,12,"#95671c",600,"right");
    arrow(450,d.queue_top-5,1,"#c39c50");
    // Entrance and a separate checkout aisle lead to the exit.
    line([[90,286],[90,306]],"#a2b8a7",5);
    line([[90,360],[90,389]],"#a2b8a7",5);
    line([[d.entrance[0],337],[148,337],[148,315]],"#b1c4b1",2,[5,5]);arrow(116,337,1,"#7d9b83");
    text("入口",38,301,14,c.ink,650);text("到达",38,321,11);
    line([[466,94],[466,338]],"#d9e4dc",2,[5,5]);
    text("收银区",487,78,13,c.ink,650);
    line([[477,366],[683,366]],"#a9c8b6",2,[5,5]);arrow(681,366,1,c.completed);
    text("出口",676,340,13,c.completed,650,"right");
    const visible=pose.cashiers.filter(slot=>slot.visible), compact=visible.length>3;
    visible.forEach(slot=>{
      const busy=slot.customer_id!==null,x=slot.x+23,y=slot.y-47,w=compact?63:108;
      round(x,y,w,60,7,busy?"#dcebf1":"#e2e9de",busy?"#bad2df":"#cfdbca");
      round(x+6,y+7,19,13,3,busy?c.serving:"#9cad9c");
      ctx.fillStyle="#fbfcf7";ctx.fillRect(x+10,y+10,11,6);
      line([[x+15,y+21],[x+15,y+25]],"#6c8b94",2);
      text(`#${slot.cashier_id}`,x+31,y+18,12,c.ink,700);
      text(busy?(compact?"结账":`顾客 #${slot.customer_id}`):"空闲",x+7,y+36,compact?10:12,busy?"#285f7c":c.muted,500);
      round(x+7,y+45,w-14,4,2,"#c7d8d9");
      if(busy&&slot.progress>0)round(x+7,y+45,(w-14)*slot.progress,4,2,c.serving);
      line([[x,y+60],[x+w,y+60]],busy?c.serving:"#a7b9a6",4);
    });
    if(pose.queue_hidden){
      round(128,309,239,29,6,"#f9e8c9");
      text(`+ ${pose.queue_hidden} 人 · 队尾 / 移动聚合`,140,328,13,"#8a5b15",600);
    }
    // Stable ID variants; no random assets or synthetic people.
    pose.entities.slice().sort((a,b)=>a.y-b.y||a.customer_id-b.customer_id).forEach(p=>{
      ctx.save();ctx.translate(p.x,p.y);ctx.globalAlpha=p.opacity;
      const accent=c[p.status],stride=p.moving?Math.sin(pose.timestamp*0.6+p.customer_id)*3:0;
      ctx.fillStyle="rgba(23,59,54,.09)";ctx.beginPath();ctx.ellipse(0,3,12,4,0,0,Math.PI*2);ctx.fill();
      ctx.lineCap="round";ctx.lineWidth=5;ctx.strokeStyle="#35534c";
      ctx.beginPath();ctx.moveTo(-4,-10);ctx.lineTo(-5-stride,0);ctx.moveTo(4,-10);ctx.lineTo(5+stride,0);ctx.stroke();
      round(-10,-29,20,21,5,accent);
      ctx.strokeStyle=accent;ctx.lineWidth=4;ctx.beginPath();ctx.moveTo(-10,-25);ctx.lineTo(-13,-15-stride);ctx.moveTo(10,-25);ctx.lineTo(13,-15+stride);ctx.stroke();
      ctx.fillStyle="#edcfae";ctx.beginPath();ctx.arc(0,-37,8,0,Math.PI*2);ctx.fill();
      ctx.fillStyle=c.ink;ctx.beginPath();ctx.ellipse(-1,-42,8,4.5,0,Math.PI,Math.PI*2);ctx.fill();
      ctx.fillRect(p.heading*3,-38,2,2);
      if(p.variant===1){ctx.strokeStyle=c.ink;ctx.lineWidth=1;ctx.strokeRect(-4,-39,8,3);}
      if(p.variant===2){line([[-7,-28],[7,-10]],"rgba(255,255,255,.7)",2);round(5,-16,8,9,2,"#e7ead7");}
      if(p.variant===3){line([[-7,-22],[7,-22]],"rgba(255,255,255,.65)",2);}
      text(String(p.customer_id),0,-15,9,"#fff",650,"center");
      ctx.restore();
    });
    text(`已完成 ${pose.completed} / ${run.config.customers}`,24,423,13,c.completed,650);
    const hiddenSlots=pose.cashiers.filter(slot=>!slot.visible);
    const hiddenBusy=hiddenSlots.filter(slot=>slot.customer_id!==null).length;
    text(hiddenSlots.length?`另有 ${hiddenSlots.length} 台 · ${hiddenBusy} 台忙碌`:"路径为示意 · 状态与计数来自真实事件",694,423,12,c.muted,400,"right");
  }
  window.Sim2ClipScene={SceneTimeline,queuePosition,cashierPosition,draw};
})();
