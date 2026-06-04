using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using System.Threading;
using ArcGIS.Core.CIM;
using ArcGIS.Core.Data;
using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;

namespace ProSimpleMapExport
{
    /// <summary>
    /// In-process bridge: a tiny loopback HTTP server that lets an EXTERNAL process
    /// (Claude / an MCP server) send structured commands to the LIVE ArcGIS Pro session.
    ///
    /// Why this works where external ArcPy can't: this code runs inside Pro, so it can
    /// reach Project.Current / the open maps & layouts and execute on the MCT via
    /// QueuedTask.Run. The user watches the result in the Pro window; the caller gets JSON.
    ///
    /// Uses a raw TcpListener on 127.0.0.1 (no admin / no URL-ACL needed) speaking
    /// minimal HTTP/1.1. Protocol: POST a JSON body {"command": "...", ...params}.
    /// </summary>
    internal static class BridgeServer
    {
        public const int Port = 5005;
        // Use dynamic path based on user's AppData folder instead of hardcoded path
        public static readonly string LogPath =
            Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "ProSimpleMapExport",
                "_bridge.log"
            );
        private static TcpListener _listener;
        private static Thread _thread;
        private static volatile bool _running;

        public static void Log(string msg)
        {
            try
            {
                // Ensure log directory exists before writing
                var logDir = Path.GetDirectoryName(LogPath);
                if (!Directory.Exists(logDir))
                    Directory.CreateDirectory(logDir);

                File.AppendAllText(LogPath, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {msg}\r\n");
            }
            catch { /* silently ignore logging errors */ }
        }

        public static void Start()
        {
            Log("BridgeServer.Start() called");
            if (_running) { Log("  already running"); return; }
            try
            {
                _listener = new TcpListener(IPAddress.Loopback, Port);
                _listener.Start();
                _running = true;
                _thread = new Thread(Loop) { IsBackground = true, Name = "ArcGISProBridge" };
                _thread.Start();
                Log($"  listening on 127.0.0.1:{Port}");
            }
            catch (Exception ex)
            {
                Log("  Start FAILED: " + ex);
                throw;
            }
        }

        public static void Stop()
        {
            _running = false;
            try { _listener?.Stop(); } catch { /* ignore */ }
        }

        private static void Loop()
        {
            while (_running)
            {
                TcpClient client = null;
                try { client = _listener.AcceptTcpClient(); }
                catch { if (!_running) break; else continue; }
                try { Handle(client); }
                catch { /* never let one request kill the loop */ }
                finally { try { client?.Close(); } catch { } }
            }
        }

        private static void Handle(TcpClient client)
        {
            var stream = client.GetStream();

            // --- read headers until CRLF CRLF ---
            var header = new StringBuilder();
            while (true)
            {
                int b = stream.ReadByte();
                if (b < 0) break;
                header.Append((char)b);
                if (header.Length >= 4 && header.ToString(header.Length - 4, 4) == "\r\n\r\n") break;
            }

            int contentLength = 0;
            foreach (var line in header.ToString().Split(new[] { "\r\n" }, StringSplitOptions.None))
            {
                if (line.StartsWith("Content-Length:", StringComparison.OrdinalIgnoreCase))
                    int.TryParse(line.Substring("Content-Length:".Length).Trim(), out contentLength);
            }

            string body = "";
            if (contentLength > 0)
            {
                var buf = new byte[contentLength];
                int read = 0;
                while (read < contentLength)
                {
                    int r = stream.Read(buf, read, contentLength - read);
                    if (r <= 0) break;
                    read += r;
                }
                body = Encoding.UTF8.GetString(buf, 0, read);
            }

            string json = Dispatch(body);
            var payload = Encoding.UTF8.GetBytes(json);
            var head = "HTTP/1.1 200 OK\r\n" +
                       "Content-Type: application/json; charset=utf-8\r\n" +
                       "Access-Control-Allow-Origin: *\r\n" +
                       $"Content-Length: {payload.Length}\r\n" +
                       "Connection: close\r\n\r\n";
            var headBytes = Encoding.ASCII.GetBytes(head);
            stream.Write(headBytes, 0, headBytes.Length);
            stream.Write(payload, 0, payload.Length);
            stream.Flush();
        }

        private static string Dispatch(string body)
        {
            try
            {
                string command = "ping";
                JsonElement root = default;
                if (!string.IsNullOrWhiteSpace(body))
                {
                    using var doc = JsonDocument.Parse(body);
                    root = doc.RootElement.Clone();
                    if (root.TryGetProperty("command", out var c)) command = c.GetString();
                }

                object data;
                var re = root; // capture for use inside the MCT lambdas
                switch (command)
                {
                    case "ping":
                        data = QueuedTask.Run(() => (object)DoPing()).GetAwaiter().GetResult();
                        break;
                    case "export_layout":
                        data = QueuedTask.Run(() => (object)DoExport(re)).GetAwaiter().GetResult();
                        break;
                    case "zoom_to":
                        data = QueuedTask.Run(() => (object)DoZoomTo(re)).GetAwaiter().GetResult();
                        break;
                    case "query":
                        data = QueuedTask.Run(() => (object)DoQuery(re)).GetAwaiter().GetResult();
                        break;
                    case "run_gp":
                        // ExecuteToolAsync manages its own threading; do NOT wrap in QueuedTask.
                        data = DoRunGp(re).GetAwaiter().GetResult();
                        break;
                    case "symbology":
                        data = QueuedTask.Run(() => (object)DoSymbology(re)).GetAwaiter().GetResult();
                        break;
                    default:
                        return Json(false, null, $"unknown command: {command}");
                }
                return Json(true, data, null);
            }
            catch (Exception ex)
            {
                Log("Dispatch error: " + ex);
                return Json(false, null, ex.Message);
            }
        }

        // ---- command handlers (all run on the MCT) ----

        private static object DoPing()
        {
            var proj = Project.Current;
            return new
            {
                bridge = "ProSimpleMapExport",
                version = "1.0",
                port = Port,
                project = proj?.Name,
                projectPath = proj?.URI,
                maps = proj?.GetItems<MapProjectItem>().Select(m => m.Name).ToArray() ?? Array.Empty<string>(),
                layouts = proj?.GetItems<LayoutProjectItem>().Select(l => l.Name).ToArray() ?? Array.Empty<string>(),
                activeLayout = LayoutView.Active?.Layout?.Name,
                activeMap = MapView.Active?.Map?.Name
            };
        }

        private static object DoExport(JsonElement root)
        {
            string layoutName = root.ValueKind == JsonValueKind.Object && root.TryGetProperty("layout", out var l) ? l.GetString() : null;
            string outPath = root.ValueKind == JsonValueKind.Object && root.TryGetProperty("out", out var o) ? o.GetString() : null;
            int dpi = 300;
            if (root.ValueKind == JsonValueKind.Object && root.TryGetProperty("dpi", out var d) && d.TryGetInt32(out var dv)) dpi = dv;

            if (string.IsNullOrWhiteSpace(outPath))
                throw new Exception("missing 'out' (output PDF path)");

            Layout layout = null;
            if (!string.IsNullOrWhiteSpace(layoutName))
            {
                var item = Project.Current.GetItems<LayoutProjectItem>()
                    .FirstOrDefault(i => string.Equals(i.Name, layoutName, StringComparison.OrdinalIgnoreCase));
                layout = item?.GetLayout();
                if (layout == null) throw new Exception($"layout not found: {layoutName}");
            }
            layout ??= LayoutView.Active?.Layout;
            layout ??= Project.Current.GetItems<LayoutProjectItem>().FirstOrDefault()?.GetLayout();
            if (layout == null) throw new Exception("no layout available in this project");

            var dir = Path.GetDirectoryName(outPath);
            if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir)) Directory.CreateDirectory(dir);

            var pdf = new PDFFormat { OutputFileName = outPath, Resolution = dpi, DoCompressVectorGraphics = true };
            layout.Export(pdf);

            return new
            {
                layout = layout.Name,
                output = outPath,
                dpi,
                exists = File.Exists(outPath),
                bytes = File.Exists(outPath) ? new FileInfo(outPath).Length : 0
            };
        }

        private static object DoZoomTo(JsonElement root)
        {
            string layerName = Str(root, "layer");
            if (string.IsNullOrWhiteSpace(layerName)) throw new Exception("missing 'layer'");
            string where = Str(root, "where");

            var mv = MapView.Active;
            if (mv == null)
                throw new Exception("没有活动的地图视图——请在 ArcGIS Pro 里切到一个地图标签页（不是布局）。");

            var map = mv.Map;
            var layer = map.GetLayersAsFlattenedList().OfType<FeatureLayer>()
                .FirstOrDefault(l => string.Equals(l.Name, layerName, StringComparison.OrdinalIgnoreCase));
            if (layer == null)
                throw new Exception($"活动地图「{map.Name}」里找不到要素图层: {layerName}");

            var span = TimeSpan.FromSeconds(1.5);
            if (!string.IsNullOrWhiteSpace(where))
            {
                var sel = layer.Select(new QueryFilter { WhereClause = where });
                long count = sel.GetCount();
                bool z = count > 0 && mv.ZoomToSelected(span);
                return new { map = map.Name, layer = layer.Name, where, selected = count, zoomed = z };
            }
            var extent = layer.QueryExtent();
            bool zoomed = mv.ZoomTo(extent, span);
            return new { map = map.Name, layer = layer.Name, zoomed };
        }

        private static object DoQuery(JsonElement root)
        {
            string layerName = Str(root, "layer");
            if (string.IsNullOrWhiteSpace(layerName)) throw new Exception("missing 'layer'");
            string where = Str(root, "where");
            int limit = IntOr(root, "limit", 50);

            // Find the feature layer: requested map -> active map -> first map.
            Map map = null;
            string mapName = Str(root, "map");
            if (!string.IsNullOrWhiteSpace(mapName))
                map = Project.Current.GetItems<MapProjectItem>()
                    .FirstOrDefault(m => string.Equals(m.Name, mapName, StringComparison.OrdinalIgnoreCase))?.GetMap();
            map ??= MapView.Active?.Map;
            map ??= Project.Current.GetItems<MapProjectItem>().FirstOrDefault()?.GetMap();
            if (map == null) throw new Exception("工程里没有可用的地图");

            var layer = map.GetLayersAsFlattenedList().OfType<FeatureLayer>()
                .FirstOrDefault(l => string.Equals(l.Name, layerName, StringComparison.OrdinalIgnoreCase));
            if (layer == null) throw new Exception($"地图「{map.Name}」里找不到要素图层: {layerName}");

            var rows = new List<Dictionary<string, object>>();
            var qf = string.IsNullOrWhiteSpace(where) ? null : new QueryFilter { WhereClause = where };
            using (var cursor = layer.Search(qf))
            {
                int n = 0;
                while (cursor.MoveNext())
                {
                    if (limit > 0 && n >= limit) break;
                    n++;
                    using (var row = cursor.Current)
                    {
                        var dict = new Dictionary<string, object>();
                        foreach (var f in row.GetFields())
                        {
                            if (f.FieldType == FieldType.Geometry || f.FieldType == FieldType.Blob ||
                                f.FieldType == FieldType.Raster) continue;
                            dict[f.Name] = row[f.Name];
                        }
                        rows.Add(dict);
                    }
                }
            }
            return new { map = map.Name, layer = layer.Name, where, returned = rows.Count, rows };
        }

        private static async System.Threading.Tasks.Task<object> DoRunGp(JsonElement root)
        {
            string tool = Str(root, "tool");
            if (string.IsNullOrWhiteSpace(tool))
                throw new Exception("missing 'tool' (例如 analysis.Buffer)");

            // Positional parameters as a JSON array of strings.
            var values = new List<object>();
            if (root.ValueKind == JsonValueKind.Object &&
                root.TryGetProperty("params", out var p) && p.ValueKind == JsonValueKind.Array)
            {
                foreach (var el in p.EnumerateArray())
                    values.Add(el.ValueKind == JsonValueKind.String ? el.GetString() : el.ToString());
            }

            // Use the NON-modal overload: (tool, values, environments, CancellationToken?,
            // GPToolExecuteEventHandler, flags). The 5-arg CancelableProgressor overload would
            // route through eval_modal (a modal progress dialog) and NRE when called headless.
            var valueArray = Geoprocessing.MakeValueArray(values.ToArray());
            var environments = Geoprocessing.MakeEnvironmentArray(overwriteoutput: true);
            var gpResult = await Geoprocessing.ExecuteToolAsync(
                tool, valueArray, environments,
                (System.Threading.CancellationToken?)null,
                (GPToolExecuteEventHandler)null,
                GPExecuteToolFlags.AddOutputsToMap);

            return new
            {
                tool,
                succeeded = !gpResult.IsFailed,
                errorCode = gpResult.ErrorCode,
                returnValue = gpResult.ReturnValue,
                outputs = gpResult.Values,
                messages = gpResult.Messages?.Select(m => m.Text).ToArray(),
                errorMessages = gpResult.ErrorMessages?.Select(m => m.Text).ToArray(),
            };
        }

        // NOTE: verified end to end — compiles against ArcGIS Pro 3.4 (.NET 8)
        // assemblies AND smoke-tested live: graduated colors applied to a layer in a
        // running Pro session via the bridge (returned ok:true, the map recolored).
        // Mirrors the verified headless `map symbology` semantics.
        private static object DoSymbology(JsonElement root)
        {
            string layerName = Str(root, "layer");
            if (string.IsNullOrWhiteSpace(layerName)) throw new Exception("missing 'layer'");
            string renderer = (Str(root, "renderer") ?? "").Trim().ToLowerInvariant();
            string field = Str(root, "field");
            if (string.IsNullOrWhiteSpace(field)) throw new Exception("missing 'field'");
            int classes = IntOr(root, "classes", 5);
            string method = Str(root, "method");
            string rampName = Str(root, "ramp");

            // Resolve the map: requested name -> active map -> first map.
            Map map = null;
            string mapName = Str(root, "map");
            if (!string.IsNullOrWhiteSpace(mapName))
                map = Project.Current.GetItems<MapProjectItem>()
                    .FirstOrDefault(m => string.Equals(m.Name, mapName, StringComparison.OrdinalIgnoreCase))?.GetMap();
            map ??= MapView.Active?.Map;
            map ??= Project.Current.GetItems<MapProjectItem>().FirstOrDefault()?.GetMap();
            if (map == null) throw new Exception("工程里没有可用的地图");

            var layer = map.GetLayersAsFlattenedList().OfType<FeatureLayer>()
                .FirstOrDefault(l => string.Equals(l.Name, layerName, StringComparison.OrdinalIgnoreCase));
            if (layer == null) throw new Exception($"地图「{map.Name}」里找不到要素图层: {layerName}");

            CIMColorRamp ramp = string.IsNullOrWhiteSpace(rampName) ? null : TryFindColorRamp(rampName);

            if (renderer == "graduated")
            {
                var def = new GraduatedColorsRendererDefinition
                {
                    ClassificationField = field,
                    BreakCount = classes,
                };
                if (!string.IsNullOrWhiteSpace(method)) def.ClassificationMethod = ParseMethod(method);
                if (ramp != null) def.ColorRamp = ramp;
                layer.SetRenderer(layer.CreateRenderer(def));
            }
            else if (renderer == "unique")
            {
                var def = new UniqueValueRendererDefinition(new List<string> { field });
                if (ramp != null) def.ColorRamp = ramp;
                layer.SetRenderer(layer.CreateRenderer(def));
            }
            else
            {
                throw new Exception("renderer 必须是 'graduated' 或 'unique'");
            }

            return new
            {
                map = map.Name,
                layer = layer.Name,
                renderer,
                field,
                classes = renderer == "graduated" ? classes : (int?)null,
                ramp = rampName,
            };
        }

        private static ClassificationMethod ParseMethod(string m)
        {
            switch ((m ?? "").Trim().ToLowerInvariant())
            {
                case "equalinterval": return ClassificationMethod.EqualInterval;
                case "quantile": return ClassificationMethod.Quantile;
                case "geometricinterval": return ClassificationMethod.GeometricalInterval;
                case "standarddeviation": return ClassificationMethod.StandardDeviation;
                case "naturalbreaks":
                default: return ClassificationMethod.NaturalBreaks;
            }
        }

        private static CIMColorRamp TryFindColorRamp(string name)
        {
            // Best-effort: search the project's styles for a ramp by name. On any
            // failure fall back to the renderer definition's default ramp.
            try
            {
                foreach (var style in Project.Current.GetItems<StyleProjectItem>())
                {
                    var hits = style.SearchColorRamps(name);
                    if (hits != null && hits.Count > 0) return hits[0].ColorRamp;
                }
            }
            catch { /* ignore — default ramp is fine */ }
            return null;
        }

        private static string Str(JsonElement root, string name) =>
            root.ValueKind == JsonValueKind.Object && root.TryGetProperty(name, out var v) &&
            v.ValueKind == JsonValueKind.String ? v.GetString() : null;

        private static int IntOr(JsonElement root, string name, int dflt) =>
            root.ValueKind == JsonValueKind.Object && root.TryGetProperty(name, out var v) &&
            v.ValueKind == JsonValueKind.Number && v.TryGetInt32(out var n) ? n : dflt;

        private static string Json(bool ok, object data, string error)
        {
            var obj = new Dictionary<string, object> { ["ok"] = ok };
            if (ok) obj["data"] = data; else obj["error"] = error;
            return JsonSerializer.Serialize(obj);
        }
    }
}
