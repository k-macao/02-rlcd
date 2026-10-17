import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const root = new URL('../../', import.meta.url);
const [html, locales, ui, simulator] = await Promise.all([
  readFile(new URL('host_web/index.html', root), 'utf8'),
  readFile(new URL('host_web/locales/static.js', root), 'utf8'),
  readFile(new URL('host_web/simulator-ui.js', root), 'utf8'),
  readFile(new URL('RLCD_CLOCK/simulator/main.cpp', root), 'utf8')
]);

assert.match(html, /<select id="simWeather"[^>]*>[\s\S]*?<option value="24">雾<\/option>/);
assert.match(locales, /\['雾','霧','霧','Fog'\]/);
assert.match(ui, /demo_scene\(Number\(event\.target\.value\)\)/);
assert.match(simulator, /scene >= 20 && scene <= 24/);
assert.match(simulator, /g_demo\.weather = scene - 20;/);
assert.match(simulator, /case 4: weather_theme = "fog";/);
assert.match(simulator, /strcmp\(theme,"fog"\)==0/);
assert.match(simulator, /aggregate_clock_weather_theme\(view,fog\?6/);
console.log('Aggregate clock fog scene reaches the shared firmware renderer in four locales.');
