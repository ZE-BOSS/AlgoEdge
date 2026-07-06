class RectangleRenderer {
    constructor(source) {
        this._source = source;
    }

    draw(target) {
        target.useBitmapCoordinateSpace((scope) => {
            if (!this._source.chart || !this._source.series) return;

            const ctx = scope.context;
            const p1 = this._source.p1;
            const p2 = this._source.p2;
            
            // Convert logical time and price to pixels
            const timeScale = this._source.chart.timeScale();
            const x1 = timeScale.timeToCoordinate(p1.time);
            const x2 = timeScale.timeToCoordinate(p2.time);
            const y1 = this._source.series.priceToCoordinate(p1.price);
            const y2 = this._source.series.priceToCoordinate(p2.price);
            
            if (x1 === null || x2 === null || y1 === null || y2 === null) return;
            
            ctx.fillStyle = this._source.color;
            
            const left = Math.min(x1, x2) * scope.horizontalPixelRatio;
            const right = Math.max(x1, x2) * scope.horizontalPixelRatio;
            const top = Math.min(y1, y2) * scope.verticalPixelRatio;
            const bottom = Math.max(y1, y2) * scope.verticalPixelRatio;
            
            ctx.fillRect(left, top, right - left, bottom - top);
        });
    }
}

class RectanglePaneView {
    constructor(source) {
        this._source = source;
    }
    update() {}
    renderer() { return new RectangleRenderer(this._source); }
}

export class RectanglePrimitive {
    constructor(p1, p2, color) {
        this.p1 = p1; // { time, price }
        this.p2 = p2; // { time, price }
        this.color = color;
    }
    
    attached({ chart, series, requestUpdate }) {
        this.chart = chart;
        this.series = series;
        this._requestUpdate = requestUpdate;
    }
    
    detached() {
        this.chart = null;
        this.series = null;
    }
    
    updateAllViews() {
        if (this._requestUpdate) {
            this._requestUpdate();
        }
    }
    
    paneViews() {
        return [new RectanglePaneView(this)];
    }
}
