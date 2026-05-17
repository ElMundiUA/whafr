# LFM2.5-1.2B-Instruct quality test

- Chunks tested: 20
- Mean judge score: **4.50 / 10**
- Median LFM2 latency: 1271 ms
- p95 LFM2 latency: 3262 ms
- Verdict: **REJECT — skip the local-summary layer, accept flat as final**

## Per-chunk scores

| # | source | score | latency | summary |
|---|---|---:|---:|---|
| 1 | `ae-bigquery:https://cloud.google.com/bigquery/docs/reference` | 4/10 | 3233ms | This passage outlines technical documentation for migrating BigQuery code to Pyt |
| 2 | `ae-databricks:https://docs.databricks.com/aws/en/admin/works` | 7/10 | 1158ms | This passage explains how Databricks collects and uses usage analytics from thir |
| 3 | `ae-dbt:https://docs.getdbt.com/blog` | 5/10 | 2249ms | Technical documentation guides AI tools by structuring information for clarity a |
| 4 | `ae-delta-lake:https://docs.delta.io/delta-kernel/` | 5/10 | 1957ms | This document outlines steps for migrating data and managing Delta tables using  |
| 5 | `ae-iceberg:https://iceberg.apache.org/docs/1.10.0/docs/amoro` | None/10 | 800ms |  |
| 6 | `ae-snowflake:https://docs.snowflake.com/en/developer-guide/d` | 1/10 | 236ms |  |
| 7 | `apigw-openapi-asyncapi:https://www.asyncapi.com/docs/referen` | 6/10 | 2313ms | The text covers various data types, extensions, and how tools handle unsupported |
| 8 | `arch-microservices-io:http://microservices.io/microservices/` | 7/10 | 1540ms | Discusses microservices anti-patterns and related resources. |
| 9 | `auth-keycloak:https://www.keycloak.org/2026/02/keycloak-js-2` | 6/10 | 676ms | This update resolves a regression in Keycloak JS affecting URL routing. |
| 10 | `auth-oauth-spec:https://oauth.net/2.1/` | 0/10 | 265ms |  |
| 11 | `auth-openid:https://openid.net/specs/openid-connect-core-1_0` | 2/10 | 1108ms | This document outlines OpenID standards and implementation guidelines. |
| 12 | `build-bazel:https://bazel.build/versions/8.6.0/rules/lib/top` | 3/10 | 1878ms | The passage explains how to encode structured data into a JSON-like format, hand |
| 13 | `build-nx:https://nx.dev/courses/pnpm-nx-next/lessons-13-outr` | 0/10 | 376ms |  |
| 14 | `build-turbo:https://turborepo.dev/blog` | 7/10 | 1650ms | Answer: The passage discusses updates, improvements, and features of Turborepo v |
| 15 | `build-vite:https://vite.dev/changes/per-environment-apis` | 6/10 | 2706ms | The update reorganizes API access for improved modularity. |
| 16 | `clarification-agile-manifesto:https://agilemanifesto.org/` | 6/10 | 1175ms | The passage emphasizes user-centric development and team collaboration in softwa |
| 17 | `clarification-agilealliance-glossary:https://agilealliance.o` | None/10 | 737ms |  |
| 18 | `clarification-atlassian-agile:https://www.atlassian.com/team` | 6/10 | 872ms | The passage outlines key practices for goal-setting, decision-making, project pl |
| 19 | `clarification-cucumber-docs:https://cucumber.io/blog` | 6/10 | 1271ms | Answer: The passage discusses Cucumber's role in BDD, feature flags, and automat |
| 20 | `clarification-domainlanguage:https://www.domainlanguage.com/` | 4/10 | 3262ms | The passage highlights the evolving landscape of software architecture, emphasiz |