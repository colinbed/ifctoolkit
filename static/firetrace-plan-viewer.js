(() => {
  const NS = "http://www.w3.org/2000/svg";
  const coordinates = (row) => {
    const geometry = row.working_geometry || row.source_geometry || {};
    return geometry.coordinates || geometry.footprint || [];
  };
  const pointsFor = (rows) => rows.flatMap((row) => coordinates(row)).filter((point) => Array.isArray(point) && point.length >= 2);
  const bounds = (rows) => {
    const points = pointsFor(rows);
    if (!points.length) return null;
    const xs = points.map((point) => Number(point[0])), ys = points.map((point) => Number(point[1]));
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  };

  class FiretracePlanViewer {
    constructor(svg, {gridToggle = null, onSelect = null} = {}) {
      this.svg = svg; this.gridToggle = gridToggle; this.onSelect = onSelect;
      this.rows = []; this.selectedId = null; this.selectionPoint = null; this.viewBox = null; this.defaultViewBox = null;
      this._drag = null;
      svg.addEventListener("pointerdown", (event) => { this._drag = [event.clientX, event.clientY, ...this.viewBox]; svg.setPointerCapture?.(event.pointerId); });
      svg.addEventListener("pointermove", (event) => { if (!this._drag) return; const scaleX=this._drag[4]/Math.max(svg.clientWidth,1),scaleY=this._drag[5]/Math.max(svg.clientHeight,1);this.viewBox=[this._drag[2]-(event.clientX-this._drag[0])*scaleX,this._drag[3]-(event.clientY-this._drag[1])*scaleY,this._drag[4],this._drag[5]];this._apply(); });
      const stop = () => { this._drag = null; }; svg.addEventListener("pointerup", stop); svg.addEventListener("pointercancel", stop);
      gridToggle?.addEventListener("change", () => this.render());
    }
    geometry(row) { return coordinates(row); }
    matches(row, id) { return row.id === id || row.source_ifc_object_id === id || row.ifc_global_id === id; }
    setRows(rows, selectedId = null, selectionPoint = null) { this.rows = rows || []; this.selectedId = selectedId; this.selectionPoint = selectionPoint; this.fit("storey"); }
    select(id, selectionPoint = null) { this.selectedId = id; this.selectionPoint = selectionPoint; this.render(); }
    _apply() { if (this.viewBox) this.svg.setAttribute("viewBox", this.viewBox.join(" ")); }
    fit(target = "storey") {
      const selected = this.rows.filter((row) => this.matches(row, this.selectedId));
      const fitRows = target === "selection" && selected.length && pointsFor(selected).length ? selected : this.rows;
      let box = bounds(fitRows);
      if (target === "selection" && !selected.length && this.selectionPoint) box=[this.selectionPoint[0]-1,this.selectionPoint[1]-1,this.selectionPoint[0]+1,this.selectionPoint[1]+1];
      if (!box) { this.render(); return false; }
      const width=Math.max(box[2]-box[0],1),height=Math.max(box[3]-box[1],1),padding=Math.max(width,height)*(target === "selection" ? .3 : .12);
      this.viewBox=[box[0]-padding,-(box[3]+padding),width+padding*2,height+padding*2];
      if (target === "storey") this.defaultViewBox=[...this.viewBox]; this.render(); return true;
    }
    zoom(factor) { if (!this.viewBox) return; const [x,y,w,h]=this.viewBox,cx=x+w/2,cy=y+h/2,nw=w*factor,nh=h*factor;this.viewBox=[cx-nw/2,cy-nh/2,nw,nh];this._apply(); }
    reset() { if (this.defaultViewBox) { this.viewBox=[...this.defaultViewBox]; this.render(); } else this.fit("storey"); }
    render() {
      this.svg.innerHTML = "";
      if (!pointsFor(this.rows).length) { this.svg.setAttribute("viewBox","0 0 800 560");this.svg.innerHTML='<g class="preview-empty"><text x="400" y="270" text-anchor="middle">Persisted plan geometry unavailable</text></g>';return; }
      if (this.gridToggle?.checked) { const defs=document.createElementNS(NS,"defs"),pattern=document.createElementNS(NS,"pattern"),path=document.createElementNS(NS,"path"),rect=document.createElementNS(NS,"rect");pattern.id=`plan-grid-${this.svg.id}`;pattern.setAttribute("width","10");pattern.setAttribute("height","10");pattern.setAttribute("patternUnits","userSpaceOnUse");path.setAttribute("d","M 10 0 L 0 0 0 10");path.setAttribute("class","plan-grid-line");pattern.append(path);defs.append(pattern);rect.setAttribute("width","100%");rect.setAttribute("height","100%");rect.setAttribute("fill",`url(#${pattern.id})`);rect.setAttribute("class","plan-grid");this.svg.append(defs,rect); }
      let selectedDrawn=false;
      this.rows.forEach((row) => { const points=this.geometry(row);if(!points.length)return;const group=document.createElementNS(NS,"g"),polygon=document.createElementNS(NS,"polygon"),isSelected=this.matches(row,this.selectedId);selectedDrawn ||= isSelected;group.setAttribute("class",`plan-space${isSelected?" selected":""}`);polygon.setAttribute("points",points.map((point)=>`${Number(point[0])},${-Number(point[1])}`).join(" "));const box=bounds([row]),label=document.createElementNS(NS,"text");label.setAttribute("x",(box[0]+box[2])/2);label.setAttribute("y",-(box[1]+box[3])/2);label.setAttribute("text-anchor","middle");label.textContent=row.space_number||row.name||"";group.append(polygon,label);if(this.onSelect)group.addEventListener("click",()=>this.onSelect(row));this.svg.append(group); });
      if (!selectedDrawn && this.selectionPoint) { const marker=document.createElementNS(NS,"circle");marker.setAttribute("cx",this.selectionPoint[0]);marker.setAttribute("cy",-this.selectionPoint[1]);marker.setAttribute("r","1.5");marker.setAttribute("class","plan-selection-marker");this.svg.append(marker); }
      this._apply();
    }
  }
  window.FiretracePlanViewer = FiretracePlanViewer;
  window.FiretracePlanGeometry = {coordinates, bounds};
})();
