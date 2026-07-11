import { useEffect, useRef } from 'react';
import { createChart, CandlestickSeries, createSeriesMarkers } from 'lightweight-charts';
import { RectanglePrimitive } from './CustomChartPrimitives';

export default function TradeChart({ group, timeframe = 'M5', height = 300 }) {
  const chartContainerRef = useRef();

  useEffect(() => {
    let rawChartData = group.chart_data || [];
    if (timeframe === 'H4') rawChartData = group.chart_data_h4 || [];
    if (timeframe === 'M15') rawChartData = group.chart_data_m15 || [];
    if (timeframe === 'M5') rawChartData = group.chart_data_m5 || [];
    if (timeframe === 'M1') rawChartData = group.chart_data_m1 || [];

    if (!group || !rawChartData || rawChartData.length === 0 || !chartContainerRef.current) return;
    
    // Create chart
    const chart = createChart(chartContainerRef.current, {
      height: height,
      layout: {
        background: { color: 'transparent' },
        textColor: '#D1D5DB', // var(--text-secondary)
      },
      grid: {
        vertLines: { color: 'rgba(255, 255, 255, 0.05)' },
        horzLines: { color: 'rgba(255, 255, 255, 0.05)' },
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: {
        mode: 1, // Normal
      },
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#10B981',
      downColor: '#EF4444',
      borderVisible: false,
      wickUpColor: '#10B981',
      wickDownColor: '#EF4444',
    });

    // Set data
    // Ensure data is sorted by time and time is uniquely ascending
    const uniqueData = [];
    let lastTime = 0;
    rawChartData.forEach(d => {
      if (d.time > lastTime) {
        uniqueData.push(d);
        lastTime = d.time;
      }
    });
    series.setData(uniqueData);

    // 1. Add Entry, SL, TP lines based on first sub-trade
    const trade = group.sub_trades && group.sub_trades.length > 0 ? group.sub_trades[0] : null;
    if (trade) {
      if (trade.entry_price) {
        series.createPriceLine({
          price: trade.entry_price,
          color: '#3B82F6', // Blue
          lineWidth: 2,
          lineStyle: 2, // Dashed
          axisLabelVisible: true,
          title: 'Entry',
        });
      }
      if (trade.stop_loss) {
        series.createPriceLine({
          price: trade.stop_loss,
          color: '#EF4444', // Red
          lineWidth: 2,
          lineStyle: 0, // Solid
          axisLabelVisible: true,
          title: 'SL',
        });
      }
      
      // Plot Take Profits
      group.sub_trades.forEach(sub => {
        if (sub.take_profit) {
          series.createPriceLine({
            price: sub.take_profit,
            color: '#10B981', // Green
            lineWidth: 2,
            lineStyle: 0,
            axisLabelVisible: true,
            title: `TP${sub.tp_level || ''}`,
          });
        }
      });
    }

    // 2. Add SMC Boxes (Order Blocks, FVGs)
    if (group.smc_data && group.smc_data.boxes) {
      group.smc_data.boxes.filter(b => b.timeframe === timeframe || (timeframe === 'M5' && !b.timeframe)).forEach(box => {
        let color = box.color || 'rgba(128, 128, 128, 0.2)'; // Use dynamic color if provided
        
        // Make sure boxes stretch fully to the end if not specified
        const endTime = box.end_time || (uniqueData.length > 0 ? uniqueData[uniqueData.length - 1].time : box.start_time + 86400);

        const rect = new RectanglePrimitive(
          { time: box.start_time, price: box.top },
          { time: endTime, price: box.bottom },
          color
        );
        series.attachPrimitive(rect);
      });
    }

    // 2b. Add Fibonacci (OTE) Zones
    if (group.smc_data && group.smc_data.fib_zones) {
      group.smc_data.fib_zones.filter(z => z.timeframe === timeframe || !z.timeframe).forEach(zone => {
        const endTime = zone.end_time || (uniqueData.length > 0 ? uniqueData[uniqueData.length - 1].time : zone.start_time + 86400);
        const rect = new RectanglePrimitive(
          { time: zone.start_time, price: zone.top },
          { time: endTime, price: zone.bottom },
          'rgba(234, 179, 8, 0.15)' // Golden/Yellow for Fib
        );
        series.attachPrimitive(rect);
      });
    }

    // 3. Add Entry, Exit, and SMC Markers
    const markers = [];
    if (group.smc_data && group.smc_data.markers) {
      group.smc_data.markers.filter(m => m.timeframe === timeframe || (timeframe === 'M5' && !m.timeframe)).forEach(m => {
        markers.push({
          time: m.time,
          position: m.text === 'BOS' ? 'aboveBar' : 'belowBar',
          color: '#6B7280', // Gray
          shape: 'circle',
          text: m.text,
        });
      });
    }
    
    if (group.entry_time) {
      markers.push({
        time: group.entry_time,
        position: group.direction === 'BUY' ? 'belowBar' : 'aboveBar',
        color: group.direction === 'BUY' ? '#3B82F6' : '#EF4444',
        shape: group.direction === 'BUY' ? 'arrowUp' : 'arrowDown',
        text: 'Entry',
      });
    }
    
    if (group.exit_time) {
      const exitPnl = group.combined_pnl || 0;
      markers.push({
        time: group.exit_time,
        position: group.direction === 'BUY' ? 'aboveBar' : 'belowBar',
        color: exitPnl > 0 ? '#10B981' : '#EF4444',
        shape: group.direction === 'BUY' ? 'arrowDown' : 'arrowUp',
        text: exitPnl > 0 ? 'Exit (Win)' : 'Exit (Loss)',
      });
    }

    // Sort markers by time
    markers.sort((a, b) => a.time - b.time);
    
    // Snap markers to closest data point within 5 minutes (300 seconds)
    const validMarkers = [];
    markers.forEach(m => {
      let closest = null;
      let minDiff = Infinity;
      uniqueData.forEach(d => {
        const diff = Math.abs(d.time - m.time);
        if (diff < minDiff) {
          minDiff = diff;
          closest = d;
        }
      });
      if (closest && minDiff <= 300) {
        validMarkers.push({ ...m, time: closest.time });
      }
    });

    if (validMarkers.length > 0) {
      createSeriesMarkers(series, validMarkers);
    }

    chart.timeScale().fitContent();

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth });
      }
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, [group]);

  return (
    <div style={{ position: 'relative', width: '100%', height: `${height}px` }}>
      <div 
        ref={chartContainerRef} 
        style={{ width: '100%', height: '100%', borderRadius: 'var(--radius-md)', overflow: 'hidden', border: '1px solid var(--border)', background: 'var(--bg-secondary)' }} 
      />
    </div>
  );
}
