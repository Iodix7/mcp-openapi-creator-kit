using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Xml.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

public class HeaderMap : Dictionary<string, string[]>
{
    public HeaderMap() : base(StringComparer.OrdinalIgnoreCase) {}
    public string GetValueOrDefault(string key, string fallback) =>
        TryGetValue(key, out var values) ? string.Join(",", values) : fallback;
}
public class Body
{
    public string Text;
    public T As<T>(bool preserveContent = true) =>
        typeof(T) == typeof(string) ? (T)(object)Text : JsonConvert.DeserializeObject<T>(Text);
}
public class Url { public HeaderMap Query = new HeaderMap(); }
public class Request
{
    public HeaderMap Headers = new HeaderMap();
    public Dictionary<string, string> MatchedParameters = new Dictionary<string, string>();
    public Url Url = new Url();
    public Body Body;
}
public class Identity { public string Id; }
public class Error { public string Source, Reason, PolicyId; }
public class Context
{
    public Request Request = new Request();
    public Identity Operation, Subscription;
    public Error LastError;
    public Dictionary<string, object> Variables = new Dictionary<string, object>();
}
public class GatewayFailure : Exception {}

public static class Program
{
    // Replaced by functions compiled directly from every generated policy expression.
    /* EXPRESSIONS */

    static object Evaluate(string source, Context context) =>
        Expressions.TryGetValue(source, out var expression) ? expression(context) : source;

    static bool Execute(XElement parent, Context context, JObject input, JObject result,
                        XElement inherited = null, string scope = "api")
    {
        foreach (var node in parent.Elements())
        {
            switch (node.Name.LocalName)
            {
                case "base":
                    if (inherited != null && Execute(inherited, context, input, result)) return true;
                    break;
                case "choose":
                    var chosen = node.Elements().FirstOrDefault(branch =>
                        branch.Name.LocalName == "otherwise" ||
                        (bool)Evaluate((string)branch.Attribute("condition"), context));
                    if (chosen != null && Execute(chosen, context, input, result, inherited, scope)) return true;
                    break;
                case "set-variable":
                    context.Variables[(string)node.Attribute("name")] =
                        Evaluate((string)node.Attribute("value"), context);
                    break;
                case "validate-content":
                    // APIM schema validation is NOT implemented by this probe.
                    if ((bool?)input["bodyError"] == true)
                    {
                        context.Variables["kitBodyErrors"] = new object();
                        context.LastError = new Error { PolicyId = "kit-runtime-body", Reason = "ValidationFailure" };
                        throw new GatewayFailure();
                    }
                    break;
                case "rate-limit":
                    result["limiterVisited"] = true;
                    ((JArray)result["visitedLimits"]).Add(scope);
                    // APIM distributed counters are NOT implemented by this probe.
                    if ((bool?)input["rateError"] == true && ((string)input["rateErrorScope"] ?? "api") == scope)
                    {
                        context.Variables["kitRetryAfter"] = 23.0;
                        context.LastError = new Error { PolicyId = "kit-runtime-rate", Reason = "RateLimitExceeded" };
                        throw new GatewayFailure();
                    }
                    break;
                case "return-response":
                    var headers = new JObject();
                    result["status"] = Convert.ToInt32(Evaluate((string)node.Element("set-status").Attribute("code"), context));
                    foreach (var header in node.Elements("set-header"))
                    {
                        var name = (string)Evaluate((string)header.Attribute("name"), context);
                        var action = (string)Evaluate((string)header.Attribute("exists-action"), context);
                        if (action == "delete") headers.Remove(name);
                        else if (action == "override") headers[name] = (string)Evaluate(header.Element("value").Value, context);
                        else throw new InvalidOperationException("Unexpected header action: " + action);
                    }
                    result["headers"] = headers;
                    result["body"] = JsonConvert.DeserializeObject<JObject>(
                        (string)Evaluate(node.Element("set-body").Value, context),
                        new JsonSerializerSettings { DateParseHandling = DateParseHandling.None });
                    return true;
                default:
                    throw new InvalidOperationException("Unimplemented probe policy: " + node.Name);
            }
        }
        return false;
    }

    static HeaderMap ReadHeaders(JObject source)
    {
        var map = new HeaderMap();
        if (source != null)
            foreach (var entry in source.Properties()) map[entry.Name] = entry.Value.ToObject<string[]>();
        return map;
    }

    public static void Main()
    {
        CultureInfo.CurrentCulture = CultureInfo.InvariantCulture;
        var data = JObject.Parse(Console.In.ReadToEnd());
        var output = new JArray();
        foreach (JObject input in (JArray)data["cases"])
        {
            var context = new Context {
                Operation = input["operationId"].Type == JTokenType.Null ? null :
                    new Identity { Id = (string)input["operationId"] },
                Subscription = input["subscriptionId"].Type == JTokenType.Null ? null :
                    new Identity { Id = (string)input["subscriptionId"] },
            };
            context.Request.Headers = ReadHeaders((JObject)input["headers"]);
            context.Request.Url.Query = ReadHeaders((JObject)input["query"]);
            context.Request.MatchedParameters = input["parameters"].ToObject<Dictionary<string, string>>();
            if (input["body"]?.Type == JTokenType.String) context.Request.Body = new Body { Text = (string)input["body"] };
            var result = new JObject { ["id"] = input["id"], ["limiterVisited"] = false,
                                       ["visitedLimits"] = new JArray() };
            var policy = XElement.Parse((string)data["policies"][(string)input["policy"]]);
            var operationXml = (string)data["operationPolicies"]?[(string)input["policy"]]?
                [(string)input["operationId"] ?? ""];
            var operationPolicy = operationXml == null ? null : XElement.Parse(operationXml);
            try
            {
                if (input["initialError"] is JObject error)
                {
                    context.LastError = error.ToObject<Error>();
                    Execute(policy.Element("on-error"), context, input, result);
                }
                else if (operationPolicy != null)
                    Execute(operationPolicy.Element("inbound"), context, input, result,
                            policy.Element("inbound"), "operation");
                else Execute(policy.Element("inbound"), context, input, result);
            }
            catch (GatewayFailure)
            {
                if (operationPolicy != null)
                    Execute(operationPolicy.Element("on-error"), context, input, result,
                            policy.Element("on-error"), "operation");
                else Execute(policy.Element("on-error"), context, input, result);
            }
            output.Add(result);
        }
        Console.WriteLine(output.ToString(Formatting.None));
    }
}
