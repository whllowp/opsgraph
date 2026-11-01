"""The OpsGraph ontology, expressed as UModel entity sets and links.

This is the graph contract. Every fact in the operational graph must land on
one of these object types, and every edge must use one of these relation types;
otherwise the graph cannot be queried, versioned, or reasoned over.
"""

from __future__ import annotations

from typing import Any

SCHEMA_URL = "umodel.aliyun.com"
SCHEMA_VERSION = "v0.1.0"

DOMAIN_LABELS = {
    "infra": {"en_us": "Runtime", "zh_cn": "运行时"},
    "svc": {"en_us": "Service", "zh_cn": "服务"},
    "biz": {"en_us": "Business", "zh_cn": "业务"},
    "ops": {"en_us": "Operations", "zh_cn": "运维"},
}


def f(
    name: str,
    field_type: str = "string",
    en: str = "",
    zh: str = "",
    desc_en: str = "",
    desc_zh: str = "",
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": name,
        "type": field_type,
        "display_name": {"en_us": en or name, "zh_cn": zh or en or name},
    }
    if desc_en or desc_zh:
        item["description"] = {"en_us": desc_en or desc_zh, "zh_cn": desc_zh or desc_en}
    return item


ENTITY_SETS: dict[str, dict[str, Any]] = {
    "infra.cluster": {
        "domain": "infra",
        "display": {"en_us": "Kubernetes Cluster", "zh_cn": "Kubernetes 集群"},
        "description": {
            "en_us": "A Kubernetes cluster that hosts observed workloads.",
            "zh_cn": "承载被观测工作负载的 Kubernetes 集群。",
        },
        "fields": [
            f("id", en="ID", zh="ID"),
            f("display_name", en="Name", zh="名称"),
            f("region", en="Region", zh="地域"),
            f("az_count", "integer", "Availability Zones", "可用区数量"),
            f("node_count", "integer", "Nodes", "节点数量"),
            f("kubernetes_version", en="Kubernetes Version", zh="Kubernetes 版本"),
            f("status", en="Status", zh="状态"),
        ],
    },
    "infra.node": {
        "domain": "infra",
        "display": {"en_us": "Node", "zh_cn": "节点"},
        "description": {
            "en_us": "A Kubernetes node with eBPF-observed resource pressure.",
            "zh_cn": "带有 eBPF 观测资源压力的 Kubernetes 节点。",
        },
        "fields": [
            f("id", en="ID", zh="ID"),
            f("display_name", en="Hostname", zh="主机名"),
            f("az", en="Availability Zone", zh="可用区"),
            f("instance_type", en="Instance Type", zh="实例规格"),
            f("cpu_cores", "integer", "CPU Cores", "CPU 核数"),
            f("cpu_utilization", "float", "CPU Utilization", "CPU 使用率"),
            f("memory_utilization", "float", "Memory Utilization", "内存使用率"),
            f("pod_count", "integer", "Pods", "Pod 数量"),
            f("anomaly_score", "float", "Anomaly Score", "异常分值"),
            f("onset_ts", "integer", "Anomaly Onset", "异常起始时间"),
            f("status", en="Status", zh="状态"),
        ],
    },
    "infra.namespace": {
        "domain": "infra",
        "display": {"en_us": "Namespace", "zh_cn": "命名空间"},
        "description": {
            "en_us": "A Kubernetes namespace boundary.",
            "zh_cn": "Kubernetes 命名空间边界。",
        },
        "fields": [
            f("id", en="ID", zh="ID"),
            f("display_name", en="Name", zh="名称"),
            f("team", en="Owning Team", zh="负责团队"),
            f("pod_count", "integer", "Pods", "Pod 数量"),
            f("service_count", "integer", "Services", "服务数量"),
        ],
    },
    "infra.workload": {
        "domain": "infra",
        "display": {"en_us": "Workload", "zh_cn": "工作负载"},
        "description": {
            "en_us": "A Deployment or CronJob that runs a service.",
            "zh_cn": "运行服务的工作负载（Deployment 或 CronJob）。",
        },
        "fields": [
            f("id", en="ID", zh="ID"),
            f("display_name", en="Name", zh="名称"),
            f("namespace", en="Namespace", zh="命名空间"),
            f("kind", en="Kind", zh="类型"),
            f("replicas", "integer", "Desired Replicas", "期望副本数"),
            f("ready_replicas", "integer", "Ready Replicas", "就绪副本数"),
            f("image", en="Image", zh="镜像"),
            f("service", en="Service", zh="服务"),
            f("status", en="Status", zh="状态"),
        ],
    },
    "infra.pod": {
        "domain": "infra",
        "display": {"en_us": "Pod", "zh_cn": "Pod"},
        "description": {
            "en_us": "A pod placed on a node, observed by the eBPF agent.",
            "zh_cn": "由 eBPF Agent 观测到、调度在节点上的 Pod。",
        },
        "fields": [
            f("id", en="ID", zh="ID"),
            f("display_name", en="Name", zh="名称"),
            f("namespace", en="Namespace", zh="命名空间"),
            f("workload", en="Workload", zh="工作负载"),
            f("node", en="Node", zh="节点"),
            f("az", en="Availability Zone", zh="可用区"),
            f("ip", en="Pod IP", zh="Pod IP"),
            f("phase", en="Phase", zh="阶段"),
            f("restart_count", "integer", "Restarts", "重启次数"),
            f("cpu_millicores", "float", "CPU (m)", "CPU（毫核）"),
            f("memory_mb", "float", "Memory (MB)", "内存（MB）"),
            f("status", en="Status", zh="状态"),
        ],
    },
    "svc.service": {
        "domain": "svc",
        "display": {"en_us": "Service", "zh_cn": "服务"},
        "description": {
            "en_us": "A logical service discovered from DeepFlow universal tags.",
            "zh_cn": "从 DeepFlow 通用标签中发现的逻辑服务。",
        },
        "fields": [
            f("id", en="ID", zh="ID"),
            f("display_name", en="Name", zh="名称"),
            f("namespace", en="Namespace", zh="命名空间"),
            f("tier", en="Topology Tier", zh="拓扑层级"),
            f("language", en="Language", zh="语言"),
            f("owner", en="Owner", zh="负责团队"),
            f("criticality", en="Criticality", zh="关键级别"),
            f("version", en="Version", zh="版本"),
            f("environment", en="Environment", zh="环境"),
            f("replicas", "integer", "Replicas", "副本数"),
            f("qps", "float", "Inbound QPS", "入向 QPS"),
            f("error_rate", "float", "Error Rate", "错误率"),
            f("latency_p99_ms", "float", "P99 Latency (ms)", "P99 延迟（ms）"),
            f("baseline_p99_ms", "float", "Baseline P99 (ms)", "基线 P99（ms）"),
            f("p99_delta_pct", "float", "P99 Delta (%)", "P99 变化（%）"),
            f("outbound_qps", "float", "Outbound QPS", "出向 QPS"),
            f("outbound_amp", "float", "Outbound Amplification", "出向放大倍数"),
            f("anomaly_score", "float", "Anomaly Score", "异常分值"),
            f("onset_ts", "integer", "Onset", "异常起始时间"),
            f("onset_signal", en="Onset Signal", zh="起始信号"),
            f("latency_onset_ts", "integer", "Latency Onset", "时延起始时间"),
            f(
                "amplification_onset_ts",
                "integer",
                "Amplification Onset",
                "放大起始时间",
            ),
            f("status", en="Status", zh="状态"),
            f("role", en="RCA Role", zh="根因角色"),
            f("change_risk", en="Recent Change Risk", zh="近期变更风险"),
            f("change_id", en="Recent Change", zh="近期变更"),
            f("observation_source", en="Observation Source", zh="观测来源"),
        ],
    },
    "svc.endpoint": {
        "domain": "svc",
        "display": {"en_us": "API Endpoint", "zh_cn": "API 端点"},
        "description": {
            "en_us": "An L7 API endpoint exposed by a service.",
            "zh_cn": "服务暴露的 L7 API 端点。",
        },
        "fields": [
            f("id", en="ID", zh="ID"),
            f("display_name", en="Endpoint", zh="端点"),
            f("service", en="Service", zh="服务"),
            f("method", en="Method", zh="方法"),
            f("path", en="Path", zh="路径"),
            f("protocol", en="Protocol", zh="协议"),
            f("qps", "float", "QPS", "QPS"),
            f("error_rate", "float", "Error Rate", "错误率"),
            f("latency_p99_ms", "float", "P99 Latency (ms)", "P99 延迟（ms）"),
            f("p99_delta_pct", "float", "P99 Delta (%)", "P99 变化（%）"),
            f("anomaly_score", "float", "Anomaly Score", "异常分值"),
            f("status", en="Status", zh="状态"),
        ],
    },
    "svc.external_service": {
        "domain": "svc",
        "display": {"en_us": "External Dependency", "zh_cn": "外部依赖"},
        "description": {
            "en_us": "A dependency observed by eBPF but not necessarily declared in the CMDB.",
            "zh_cn": "由 eBPF 观测到、但未必在 CMDB 中声明的依赖。",
        },
        "fields": [
            f("id", en="ID", zh="ID"),
            f("display_name", en="Name", zh="名称"),
            f("domain", en="DNS Domain", zh="DNS 域名"),
            f("kind", en="Kind", zh="类型"),
            f("provider", en="Provider", zh="提供方"),
            f("criticality", en="Criticality", zh="关键级别"),
            f("qps", "float", "QPS", "QPS"),
            f("error_rate", "float", "Error Rate", "错误率"),
            f("latency_p99_ms", "float", "P99 Latency (ms)", "P99 延迟（ms）"),
            f("anomaly_score", "float", "Anomaly Score", "异常分值"),
            f("declared", "boolean", "Declared", "已声明"),
            f("shadow", "boolean", "Shadow Dependency", "影子依赖"),
            f("status", en="Status", zh="状态"),
        ],
    },
    "biz.journey": {
        "domain": "biz",
        "display": {"en_us": "Business Journey", "zh_cn": "业务旅程"},
        "description": {
            "en_us": "A revenue-bearing user journey that depends on services.",
            "zh_cn": "依赖服务、承担营收的用户旅程。",
        },
        "fields": [
            f("id", en="ID", zh="ID"),
            f("display_name", en="Journey", zh="旅程"),
            f("tier", en="Tier", zh="层级"),
            f("owner", en="Owner", zh="负责团队"),
            f("services", en="Services", zh="依赖服务"),
            f("conversion_impact", en="Conversion Impact", zh="转化影响"),
            f("revenue_exposure", en="Revenue Exposure", zh="营收敞口"),
            f("impacted", "boolean", "Impacted", "是否受影响"),
            f("impacted_qps", "float", "Impacted QPS", "受影响 QPS"),
            f("status", en="Status", zh="状态"),
        ],
    },
    "ops.incident": {
        "domain": "ops",
        "display": {"en_us": "Incident", "zh_cn": "故障事件"},
        "description": {
            "en_us": "An operational incident under investigation.",
            "zh_cn": "正在排查的运维故障事件。",
        },
        "fields": [
            f("id", en="ID", zh="ID"),
            f("display_name", en="Title", zh="标题"),
            f("severity", en="Severity", zh="严重级别"),
            f("status", en="Status", zh="状态"),
            f("started_at", en="Started At", zh="开始时间"),
            f("detected_at", en="Detected At", zh="发现时间"),
            f("started_ts", "integer", "Started Timestamp", "开始时间戳"),
            f("victim", en="Victim Service", zh="受影响服务"),
            f("summary", en="Summary", zh="摘要"),
            f("impact", en="Impact", zh="影响"),
            f("owner", en="Owner", zh="负责人"),
            f("root_cause", en="Root Cause", zh="根因"),
        ],
    },
    "ops.change": {
        "domain": "ops",
        "display": {"en_us": "Change", "zh_cn": "变更"},
        "description": {
            "en_us": "A deployment or configuration change that can alter graph behavior.",
            "zh_cn": "可能改变图行为的部署或配置变更。",
        },
        "fields": [
            f("id", en="ID", zh="ID"),
            f("display_name", en="Change", zh="变更"),
            f("service", en="Target Service", zh="目标服务"),
            f("change_type", en="Change Type", zh="变更类型"),
            f("applied_at", en="Applied At", zh="生效时间"),
            f("applied_ts", "integer", "Applied Timestamp", "生效时间戳"),
            f("risk", en="Risk", zh="风险"),
            f("summary", en="Summary", zh="摘要"),
            f("detail", en="Detail", zh="详情"),
            f("rollback", en="Rollback", zh="回滚方式"),
            f("status", en="Status", zh="状态"),
        ],
    },
    "ops.alert": {
        "domain": "ops",
        "display": {"en_us": "Alert", "zh_cn": "告警"},
        "description": {
            "en_us": "A signal that opened or amplified an incident.",
            "zh_cn": "触发或放大故障的信号。",
        },
        "fields": [
            f("id", en="ID", zh="ID"),
            f("display_name", en="Alert", zh="告警"),
            f("target", en="Target", zh="目标"),
            f("target_type", en="Target Type", zh="目标类型"),
            f("severity", en="Severity", zh="严重级别"),
            f("status", en="Status", zh="状态"),
            f("fired_at", en="Fired At", zh="触发时间"),
            f("fired_ts", "integer", "Fired Timestamp", "触发时间戳"),
            f("signal", en="Signal", zh="信号"),
            f("value", en="Value", zh="值"),
            f("summary", en="Summary", zh="摘要"),
        ],
    },
}

LINKS: list[dict[str, Any]] = [
    {
        "name": "infra.cluster_contains_infra.node",
        "domain": "infra",
        "src": "infra.cluster",
        "dest": "infra.node",
        "type": "contains",
        "display": {"en_us": "Cluster contains Node", "zh_cn": "集群包含节点"},
        "description": {
            "en_us": "A node belongs to a cluster.",
            "zh_cn": "节点属于某个集群。",
        },
    },
    {
        "name": "infra.namespace_contains_infra.workload",
        "domain": "infra",
        "src": "infra.namespace",
        "dest": "infra.workload",
        "type": "contains",
        "display": {
            "en_us": "Namespace contains Workload",
            "zh_cn": "命名空间包含工作负载",
        },
        "description": {
            "en_us": "A workload runs inside a namespace.",
            "zh_cn": "工作负载运行在命名空间中。",
        },
    },
    {
        "name": "infra.workload_contains_infra.pod",
        "domain": "infra",
        "src": "infra.workload",
        "dest": "infra.pod",
        "type": "contains",
        "display": {"en_us": "Workload contains Pod", "zh_cn": "工作负载包含 Pod"},
        "description": {
            "en_us": "A pod is created by a workload.",
            "zh_cn": "Pod 由工作负载创建。",
        },
    },
    {
        "name": "infra.pod_scheduled_on_infra.node",
        "domain": "infra",
        "src": "infra.pod",
        "dest": "infra.node",
        "type": "scheduled_on",
        "display": {"en_us": "Pod scheduled on Node", "zh_cn": "Pod 调度到节点"},
        "description": {
            "en_us": "A pod runs on exactly one node.",
            "zh_cn": "Pod 运行在唯一节点上。",
        },
    },
    {
        "name": "svc.service_runs_as_infra.workload",
        "domain": "svc",
        "src": "svc.service",
        "dest": "infra.workload",
        "type": "runs_as",
        "display": {"en_us": "Service runs as Workload", "zh_cn": "服务由工作负载运行"},
        "description": {
            "en_us": "Maps a logical service to its Kubernetes workload.",
            "zh_cn": "将逻辑服务映射到 Kubernetes 工作负载。",
        },
    },
    {
        "name": "svc.service_exposes_svc.endpoint",
        "domain": "svc",
        "src": "svc.service",
        "dest": "svc.endpoint",
        "type": "exposes",
        "display": {"en_us": "Service exposes Endpoint", "zh_cn": "服务暴露端点"},
        "description": {
            "en_us": "An API endpoint served by a service.",
            "zh_cn": "服务提供的 API 端点。",
        },
    },
    {
        "name": "svc.service_calls_svc.service",
        "domain": "svc",
        "src": "svc.service",
        "dest": "svc.service",
        "type": "calls",
        "display": {"en_us": "Service calls Service", "zh_cn": "服务调用服务"},
        "description": {
            "en_us": "An L7 call edge observed by eBPF. The relation carries its own RPS, error rate, P99, baseline and onset evidence.",
            "zh_cn": "eBPF 观测到的 L7 调用边，关系自身携带 RPS、错误率、P99、基线与起始时间证据。",
        },
    },
    {
        "name": "svc.service_calls_svc.external_service",
        "domain": "svc",
        "src": "svc.service",
        "dest": "svc.external_service",
        "type": "calls",
        "display": {
            "en_us": "Service calls External Dependency",
            "zh_cn": "服务调用外部依赖",
        },
        "description": {
            "en_us": "An outbound L7 dependency on a domain, database, or third-party API.",
            "zh_cn": "对域名、数据库或第三方 API 的出向 L7 依赖。",
        },
    },
    {
        "name": "svc.service_calls_endpoint_svc.endpoint",
        "domain": "svc",
        "src": "svc.service",
        "dest": "svc.endpoint",
        "type": "calls_endpoint",
        "display": {"en_us": "Service calls Endpoint", "zh_cn": "服务调用端点"},
        "description": {
            "en_us": "Endpoint-level call evidence, used to localize a failing API.",
            "zh_cn": "端点级调用证据，用于定位具体失败的 API。",
        },
    },
    {
        "name": "biz.journey_depends_on_svc.service",
        "domain": "biz",
        "src": "biz.journey",
        "dest": "svc.service",
        "type": "depends_on",
        "display": {"en_us": "Journey depends on Service", "zh_cn": "业务旅程依赖服务"},
        "description": {
            "en_us": "Declared business dependency used to compute blast radius.",
            "zh_cn": "用于计算爆炸半径的声明式业务依赖。",
        },
    },
    {
        "name": "ops.incident_affects_svc.service",
        "domain": "ops",
        "src": "ops.incident",
        "dest": "svc.service",
        "type": "affects",
        "display": {"en_us": "Incident affects Service", "zh_cn": "故障影响服务"},
        "description": {
            "en_us": "The service where the incident was detected.",
            "zh_cn": "检测到故障的服务。",
        },
    },
    {
        "name": "ops.change_targets_svc.service",
        "domain": "ops",
        "src": "ops.change",
        "dest": "svc.service",
        "type": "targets",
        "display": {"en_us": "Change targets Service", "zh_cn": "变更作用于服务"},
        "description": {
            "en_us": "A change applied to a service.",
            "zh_cn": "作用于服务的变更。",
        },
    },
    {
        "name": "ops.alert_targets_svc.service",
        "domain": "ops",
        "src": "ops.alert",
        "dest": "svc.service",
        "type": "targets",
        "display": {"en_us": "Alert targets Service", "zh_cn": "告警指向服务"},
        "description": {"en_us": "A service-level alert.", "zh_cn": "服务级告警。"},
    },
    {
        "name": "ops.alert_targets_infra.node",
        "domain": "ops",
        "src": "ops.alert",
        "dest": "infra.node",
        "type": "targets",
        "display": {"en_us": "Alert targets Node", "zh_cn": "告警指向节点"},
        "description": {"en_us": "A node-level alert.", "zh_cn": "节点级告警。"},
    },
]


def entity_set_spec(name: str) -> dict[str, Any]:
    item = ENTITY_SETS[name]
    return {
        "fields": item["fields"],
        "primary_key_fields": ["id"],
        "id_generator": "id",
        "name_fields": ["display_name"],
        "ordered_fields": [field["name"] for field in item["fields"]],
        "first_observed_time_field": "__first_observed_time__",
        "last_observed_time_field": "__last_observed_time__",
    }
