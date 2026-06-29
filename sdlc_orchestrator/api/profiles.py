"""sdlc_orchestrator/api/profiles.py
Technology profiles — one per application archetype.
Built-in profiles are versioned in code; custom profiles are stored in Redis.
Child profiles declare `extends` and only override differing fields.
"""
import json

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/profiles", tags=["profiles"])

_redis: aioredis.Redis = None


def init_profiles_router(redis_client: aioredis.Redis) -> None:
    global _redis
    _redis = redis_client


# ── Built-in profiles ─────────────────────────────────────────────────────────
# Child profiles only list fields that DIFFER from their parent.
# Use resolve_profile() to get fully-merged data.

TECH_PROFILES: list[dict] = [

    # ── Backend ────────────────────────────────────────────────────────────────
    {
        "id": "spring-boot-rest-api",
        "label": "Spring Boot REST API",
        "category": "Backend",
        "language": "Java",
        "framework": "Spring Boot",
        "build_tool": "Maven",
        "test_framework": "JUnit 5 + Mockito",
        "deploy_target": "ECS / Kubernetes",
        "build_command": "mvn clean package -DskipTests",
        "test_command": "mvn test",
        "dev_command": "mvn spring-boot:run",
        "dev_port": 8080,
        "e2e_strategy": "playwright",
        "mcp_servers": {
            "implement": ["github"],
            "review":    ["github"],
            "e2e":       ["playwright"],
        },
        "conventions": {
            "controllers": "src/main/java/**/controller/",
            "services":    "src/main/java/**/service/",
            "entities":    "src/main/java/**/model/",
            "repos":       "src/main/java/**/repository/",
            "config":      "src/main/java/**/config/",
            "tests":       "src/test/java/",
        },
        "detect_files": ["pom.xml", "src/main/resources/application.yml"],
        "review_rules": (
            "ARCHITECTURE: Business logic in @RestController → move to @Service. "
            "Direct repository calls from controllers bypassing service layer. "
            "Missing @Transactional on methods doing multiple DB writes.\n"
            "DATABASE: N+1 queries — missing JOIN FETCH or @BatchSize. "
            "Unbounded findAll() without pagination on large tables.\n"
            "SECURITY: Secrets hardcoded or in committed application.yml. "
            "Missing @Valid on @RequestBody. SQL built with string concat (SQLi). "
            "Missing @PreAuthorize on protected endpoints.\n"
            "RELIABILITY: Bare Exception catch without logging. "
            "Resource leaks — streams or connections not closed."
        ),
    },
    {
        "id": "python-fastapi",
        "label": "Python FastAPI",
        "category": "Backend",
        "language": "Python",
        "framework": "FastAPI",
        "build_tool": "pip / Poetry",
        "test_framework": "pytest",
        "deploy_target": "ECS / Cloud Run",
        "build_command": "pip install -r requirements.txt",
        "test_command": "pytest",
        "dev_command": "uvicorn main:app --reload",
        "dev_port": 8000,
        "e2e_strategy": "playwright",
        "mcp_servers": {
            "implement": ["github"],
            "review":    ["github"],
            "e2e":       ["playwright"],
        },
        "conventions": {
            "routers":  "app/routers/ or src/routers/",
            "models":   "app/models/",
            "services": "app/services/",
            "tests":    "tests/",
        },
        "detect_files": ["requirements.txt", "pyproject.toml", "main.py"],
        "review_rules": (
            "ARCHITECTURE: Business logic in route handlers → move to service layer. "
            "Missing Pydantic validation on request/response models.\n"
            "ASYNC: Blocking I/O in async def (use asyncio/httpx not requests). "
            "Missing await on coroutines.\n"
            "SECURITY: Hardcoded secrets. Missing auth dependency on protected routes. "
            "SQL built with f-strings (SQLi) — use SQLAlchemy parameterized queries.\n"
            "RELIABILITY: Unhandled exceptions reaching client without HTTPException wrapper."
        ),
    },
    {
        "id": "nodejs-express",
        "label": "Node.js Express API",
        "category": "Backend",
        "language": "TypeScript / JavaScript",
        "framework": "Express",
        "build_tool": "npm",
        "test_framework": "Jest",
        "deploy_target": "ECS / App Service",
        "build_command": "npm run build",
        "test_command": "npm test",
        "dev_command": "npm run dev",
        "dev_port": 3000,
        "e2e_strategy": "playwright",
        "mcp_servers": {
            "implement": ["github"],
            "review":    ["github"],
            "e2e":       ["playwright"],
        },
        "conventions": {
            "routes":      "src/routes/",
            "controllers": "src/controllers/",
            "services":    "src/services/",
            "models":      "src/models/",
            "tests":       "src/__tests__/ or tests/",
        },
        "detect_files": ["package.json", "tsconfig.json"],
        "review_rules": (
            "ASYNC: Unhandled promise rejections — missing .catch() or try/catch in async handlers. "
            "Missing next(err) in error middleware.\n"
            "SECURITY: Hardcoded secrets. Missing input validation (Joi/Zod). "
            "NoSQL/SQL injection via template string queries. "
            "CORS wildcard (*) in production.\n"
            "RELIABILITY: No error-handling middleware. "
            "Synchronous fs.readFileSync in request handlers."
        ),
    },
    {
        "id": "go-service",
        "label": "Go Microservice",
        "category": "Backend",
        "language": "Go",
        "framework": "Gin / Chi / stdlib",
        "build_tool": "go build",
        "test_framework": "go test",
        "deploy_target": "ECS / Kubernetes",
        "build_command": "go build ./...",
        "test_command": "go test ./...",
        "dev_command": "go run main.go",
        "dev_port": 8080,
        "e2e_strategy": "playwright",
        "mcp_servers": {
            "implement": ["github"],
            "review":    ["github"],
            "e2e":       ["playwright"],
        },
        "conventions": {
            "handlers": "internal/handler/ or cmd/",
            "services": "internal/service/",
            "models":   "internal/model/ or pkg/",
            "tests":    "*_test.go alongside source",
        },
        "detect_files": ["go.mod", "go.sum"],
        "review_rules": (
            "CONCURRENCY: Race conditions — shared mutable state without mutex. "
            "Goroutine leaks — goroutines started without completion mechanism. "
            "Channel deadlocks.\n"
            "ERROR HANDLING: Ignoring errors with _ without explicit comment. "
            "Missing context wrapping (fmt.Errorf('context: %w', err)).\n"
            "PERFORMANCE: Missing defer for cleanup (file.Close, mutex.Unlock). "
            "Large structs by value in hot paths.\n"
            "SECURITY: Hardcoded credentials. Missing input validation."
        ),
    },

    # ── Frontend ───────────────────────────────────────────────────────────────
    {
        "id": "react-vite-spa",
        "label": "React SPA (Vite)",
        "category": "Frontend",
        "language": "TypeScript / JavaScript",
        "framework": "React + Vite",
        "build_tool": "npm / Vite",
        "test_framework": "Jest + React Testing Library",
        "deploy_target": "S3 + CloudFront / Vercel / Netlify",
        "build_command": "npm run build",
        "test_command": "npm test",
        "dev_command": "npm run dev",
        "dev_port": 5173,
        "e2e_strategy": "playwright",
        "mcp_servers": {
            "implement": ["github"],
            "review":    ["github"],
            "e2e":       ["playwright"],
        },
        "conventions": {
            "components": "src/components/",
            "pages":      "src/pages/",
            "hooks":      "src/hooks/",
            "utils":      "src/utils/",
            "tests":      "src/__tests__/ or *.test.tsx",
        },
        "detect_files": ["package.json", "vite.config.ts", "vite.config.js"],
        "review_rules": (
            "REACT: useEffect with missing dependencies (stale closures). "
            "Direct DOM mutation instead of state. State mutations instead of new objects. "
            "Key prop missing or using array index on reordered lists.\n"
            "PERFORMANCE: Expensive computations without useMemo. "
            "Functions recreated every render without useCallback when passed as props. "
            "No route-level code splitting (React.lazy + Suspense).\n"
            "ACCESSIBILITY: Missing aria-label on icon buttons. "
            "Interactive elements without keyboard support.\n"
            "SECURITY: dangerouslySetInnerHTML with user content (XSS). "
            "Auth tokens in localStorage (use httpOnly cookies)."
        ),
    },
    {
        "id": "nextjs",
        "label": "Next.js App",
        "category": "Frontend",
        "language": "TypeScript",
        "framework": "Next.js",
        "build_tool": "npm",
        "test_framework": "Jest + Cypress",
        "deploy_target": "Vercel / ECS",
        "build_command": "npm run build",
        "test_command": "npm test",
        "dev_command": "npm run dev",
        "dev_port": 3000,
        "e2e_strategy": "playwright",
        "mcp_servers": {
            "implement": ["github"],
            "review":    ["github"],
            "e2e":       ["playwright"],
        },
        "conventions": {
            "pages":      "app/ or pages/",
            "components": "components/",
            "api_routes": "app/api/ or pages/api/",
        },
        "detect_files": ["next.config.js", "next.config.ts", "next.config.mjs"],
        "review_rules": (
            "RENDERING: Data fetched client-side that should be Server Component. "
            "'use client' on components that don't need interactivity. "
            "Missing Suspense around async Server Components.\n"
            "PERFORMANCE: next/image not used for images. next/font not used for fonts. "
            "Missing generateStaticParams for static-capable dynamic routes.\n"
            "SECURITY: NEXT_PUBLIC_ env vars with sensitive values. "
            "Missing auth check on /api/ mutation routes. "
            "Missing CSRF protection."
        ),
    },

    # ── Serverless ─────────────────────────────────────────────────────────────
    {
        "id": "aws-lambda-python",
        "label": "AWS Lambda (Python)",
        "category": "Serverless",
        "language": "Python",
        "framework": "AWS Lambda + SAM / Serverless",
        "build_tool": "SAM CLI / Serverless Framework",
        "test_framework": "pytest + moto",
        "deploy_target": "AWS Lambda",
        "build_command": "sam build",
        "test_command": "pytest",
        "dev_command": "sam local invoke",
        "dev_port": 0,
        "e2e_strategy": "jest-only",
        "mcp_servers": {
            "implement": ["github", "aws-mcp"],
            "review":    ["github"],
            "e2e":       [],
        },
        "conventions": {
            "handlers": "src/ or functions/",
            "layers":   "layers/",
            "tests":    "tests/",
            "infra":    "template.yaml or serverless.yml",
        },
        "detect_files": ["template.yaml", "serverless.yml", "handler.py"],
        "review_rules": (
            "COLD START: Heavy imports at module level. "
            "SDK clients inside handler (move to module scope). Lambda package > 10 MB.\n"
            "CORRECTNESS: Missing error handling — unhandled exceptions crash invocation. "
            "Returning non-serializable objects (datetime, Decimal).\n"
            "AWS: IAM Action '*' or Resource '*'. "
            "Secrets in env vars instead of Secrets Manager. "
            "Missing DLQ for async invocations. Timeout too low for downstream calls.\n"
            "TESTING: Tests not using moto. Missing test for error path."
        ),
    },
    {
        "id": "aws-lambda-nodejs",
        "label": "AWS Lambda (Node.js)",
        "category": "Serverless",
        "language": "TypeScript / JavaScript",
        "framework": "AWS Lambda + CDK / SAM",
        "build_tool": "npm + esbuild",
        "test_framework": "Jest",
        "deploy_target": "AWS Lambda",
        "build_command": "npm run build",
        "test_command": "npm test",
        "dev_command": "sam local invoke",
        "dev_port": 0,
        "e2e_strategy": "jest-only",
        "mcp_servers": {
            "implement": ["github", "aws-mcp"],
            "review":    ["github"],
            "e2e":       [],
        },
        "conventions": {
            "handlers": "src/ or functions/",
            "tests":    "tests/",
            "infra":    "lib/ (CDK) or template.yaml (SAM)",
        },
        "detect_files": ["template.yaml", "cdk.json", "serverless.yml"],
        "review_rules": (
            "COLD START: SDK clients inside handler. Excessive deps. Missing tree shaking.\n"
            "CORRECTNESS: Async handler must return a value — missing return causes timeout. "
            "Unhandled promise rejections.\n"
            "AWS: IAM least-privilege violations. Secrets not in Secrets Manager. "
            "Missing DLQ for async invocations.\n"
            "TESTING: Missing Jest mock for AWS SDK. No test for event parsing edge cases."
        ),
    },

    # ── Salesforce ─────────────────────────────────────────────────────────────
    {
        "id": "salesforce-core",
        "label": "Salesforce Core (Sales/Service Cloud)",
        "category": "Salesforce",
        "language": "Apex / LWC / Aura",
        "framework": "Salesforce DX",
        "build_tool": "sf CLI",
        "test_framework": "Apex Tests",
        "deploy_target": "Salesforce Org (sandbox → staging → prod)",
        "build_command": "sf project deploy start --dry-run",
        "test_command": "sf apex run test --code-coverage --result-format human",
        "dev_command": "sf org open",
        "dev_port": 0,
        "e2e_strategy": "playwright",
        "mcp_servers": {
            "implement": ["github", "salesforce-mcp"],
            "test":      ["salesforce-mcp"],
            "review":    ["github"],
            "e2e":       ["playwright"],
        },
        "conventions": {
            "apex_classes":    "force-app/main/default/classes/",
            "lwc":             "force-app/main/default/lwc/",
            "aura":            "force-app/main/default/aura/",
            "flows":           "force-app/main/default/flows/",
            "objects":         "force-app/main/default/objects/",
            "permission_sets": "force-app/main/default/permissionsets/",
            "tests":           "force-app/test/",
        },
        "detect_files": ["sfdx-project.json", "force-app/main/default/classes"],
        "review_rules": (
            "GOVERNOR LIMITS (REJECT — throws LimitException in production): "
            "SOQL queries inside for loops. DML inside for loops. "
            "Trigger handlers not bulk-safe (must handle List<SObject>).\n"
            "TRIGGERS: Logic in trigger body — all logic must be in a handler class. "
            "No before/after separation.\n"
            "APEX QUALITY: Test classes without System.assert() — meaningless coverage. "
            "Coverage < 75% blocks deployment. @isTest(SeeAllData=true). "
            "Hardcoded Record Type/Profile IDs. Missing @HttpCalloutMock.\n"
            "LWC: console.log in production. Imperative Apex calls not in try/catch."
        ),
    },
    {
        "id": "salesforce-b2c-commerce",
        "label": "Salesforce B2C Commerce (SFCC Classic / SFRA)",
        "category": "Salesforce",
        "language": "JavaScript / ISML",
        "framework": "SFCC Cartridge Architecture (SFRA)",
        "build_tool": "sfcc-ci / OCAPI",
        "test_framework": "Mocha + Chai",
        "deploy_target": "SFCC Sandbox → Staging → Production",
        "build_command": "npm run build",
        "test_command": "npm test",
        "dev_command": "npm start",
        "dev_port": 3000,
        "e2e_strategy": "playwright",
        "mcp_servers": {
            "implement": ["github"],
            "review":    ["github"],
            "e2e":       ["playwright"],
        },
        "conventions": {
            "cartridges":  "cartridges/",
            "controllers": "cartridges/*/cartridge/controllers/",
            "models":      "cartridges/*/cartridge/models/",
            "templates":   "cartridges/*/cartridge/templates/",
            "scripts":     "cartridges/*/cartridge/scripts/",
            "static":      "cartridges/*/cartridge/static/",
        },
        "detect_files": ["package.json", "cartridges/", "dw.json"],
        "review_rules": (
            "CARTRIDGE: Business logic in controllers — move to model/helper scripts. "
            "Overriding base cartridge files directly instead of extending via cartridge chain.\n"
            "PERFORMANCE: Sync remote includes in ISML. Missing cache directives on cacheable pages.\n"
            "SECURITY: Missing CSRF token on form submissions. "
            "User input rendered in ISML without encoding (XSS — use Encoding.htmlEncode). "
            "Sensitive data in session attributes without timeout.\n"
            "TESTING: Missing unit tests for model/helper scripts."
        ),
    },
    {
        "id": "sfcc-pwakit",
        "label": "Salesforce B2C Commerce — PWA Kit Storefront",
        "category": "Salesforce",
        "extends": "salesforce-b2c-commerce",
        "language": "TypeScript / React",
        "framework": "PWA Kit (Retail React App)",
        "test_framework": "Jest + React Testing Library",
        "deploy_target": "Salesforce Managed Runtime (MRT)",
        "dev_port": 3000,
        "e2e_strategy": "playwright",
        "mcp_servers": {
            "implement": ["github", "salesforce-mcp"],
            "test":      ["salesforce-mcp"],
            "review":    ["github"],
            "e2e":       ["playwright"],
        },
        "conventions": {
            "components": "app/components/",
            "pages":      "app/pages/",
            "hooks":      "app/hooks/",
            "utils":      "app/utils/",
            "api_config": "app/config/",
            "routes":     "app/routes.jsx",
        },
        "detect_files": ["pwa-kit-runtime", "retail-react-app", "package.json"],
        "review_rules": (
            "PWA KIT: Bypass Commerce SDK hooks (use useProduct, useCategory, getConfig). "
            "Custom API calls not following app/utils/url.js MRT patterns. "
            "MRT env vars set via process.env directly instead of config/local.js. "
            "Missing error boundaries around page components.\n"
            "REACT: useEffect missing dependencies. "
            "No route-level code splitting (React.lazy + Suspense).\n"
            "COMMERCE: Basket mutations not optimistically updated. "
            "Hardcoded locale/currency strings (use useIntl).\n"
            "SECURITY: Shopper JWT in localStorage (must use SDK session cookie). "
            "Hardcoded SCAPI client IDs in frontend code."
        ),
    },
    {
        "id": "sfcc-headless",
        "label": "Salesforce B2C Commerce — Headless + SCAPI",
        "category": "Salesforce",
        "extends": "salesforce-b2c-commerce",
        "language": "TypeScript",
        "framework": "Next.js + Salesforce SCAPI",
        "test_framework": "Jest + Cypress",
        "deploy_target": "Vercel / CDN (frontend) + SFCC (APIs)",
        "dev_port": 3000,
        "e2e_strategy": "playwright",
        "mcp_servers": {
            "implement": ["github"],
            "review":    ["github"],
            "e2e":       ["playwright"],
        },
        "conventions": {
            "pages":     "app/ or pages/",
            "api_proxy": "app/api/ (SCAPI proxy routes)",
            "hooks":     "hooks/",
            "lib":       "lib/ (Commerce SDK wrappers)",
        },
        "detect_files": ["next.config.js", "package.json"],
        "review_rules": (
            "SCAPI: Shopper tokens in localStorage (must be httpOnly cookies via API route proxy). "
            "SCAPI client secret in frontend code. "
            "Missing CORS config for SCAPI calls from browser. "
            "Not refreshing Shopper JWT before 24h expiry.\n"
            "NEXT.JS: Product/category data fetched client-side (should be Server Component or ISR). "
            "Missing ISR for product pages.\n"
            "SECURITY: SCAPI proxy route not validating session. "
            "Basket ID exposed in URL params."
        ),
    },

    # ── Mobile ──────────────────────────────────────────────────────────────────
    {
        "id": "react-native",
        "label": "React Native (iOS + Android)",
        "category": "Mobile",
        "language": "TypeScript",
        "framework": "React Native",
        "build_tool": "npm + Metro",
        "test_framework": "Jest + Detox",
        "deploy_target": "iOS App Store / Google Play",
        "build_command": "npx react-native build-ios --configuration Release",
        "test_command": "npx jest && npx detox test",
        "dev_command": "npx react-native start",
        "dev_port": 8081,
        "e2e_strategy": "detox",
        "mcp_servers": {
            "implement": ["github"],
            "review":    ["github"],
            "e2e":       ["appium-mcp"],
        },
        "conventions": {
            "screens":    "src/screens/",
            "components": "src/components/",
            "navigation": "src/navigation/",
            "hooks":      "src/hooks/",
            "services":   "src/services/",
            "tests":      "__tests__/ or *.test.tsx",
            "e2e":        "e2e/ (Detox specs)",
        },
        "detect_files": ["android/", "ios/", "metro.config.js", "react-native.config.js"],
        "review_rules": (
            "PERFORMANCE: FlatList without keyExtractor or windowSize. "
            "Heavy computation on JS thread (use Reanimated runOnUI for animations). "
            "Missing React.memo on pure list item components.\n"
            "NATIVE BRIDGE: Missing null checks on native module results. "
            "Calling native modules on unmounted components.\n"
            "SECURITY: Sensitive data in AsyncStorage without encryption "
            "(use react-native-encrypted-storage). HTTP requests in cleartext. "
            "API keys in JS bundle.\n"
            "TESTING: Missing Detox spec for main user journey. "
            "Jest mocks for native modules not in jest setup."
        ),
    },
    {
        "id": "flutter",
        "label": "Flutter (iOS + Android + Web)",
        "category": "Mobile",
        "language": "Dart",
        "framework": "Flutter",
        "build_tool": "Flutter CLI",
        "test_framework": "flutter_test + integration_test",
        "deploy_target": "iOS App Store / Google Play / Web",
        "build_command": "flutter build apk --release",
        "test_command": "flutter test && flutter test integration_test/",
        "dev_command": "flutter run",
        "dev_port": 0,
        "e2e_strategy": "flutter-integration",
        "mcp_servers": {
            "implement": ["github"],
            "review":    ["github"],
            "e2e":       [],
        },
        "conventions": {
            "screens":   "lib/screens/ or lib/pages/",
            "widgets":   "lib/widgets/",
            "models":    "lib/models/",
            "services":  "lib/services/",
            "providers": "lib/providers/ (Riverpod / BLoC)",
            "tests":     "test/",
            "e2e":       "integration_test/",
        },
        "detect_files": ["pubspec.yaml", "lib/main.dart"],
        "review_rules": (
            "STATE: setState for app-level state (use Riverpod/BLoC). "
            "dispose() not called for controllers/animations/subscriptions. "
            "BuildContext used across async gaps without mounted check.\n"
            "PERFORMANCE: const constructor missing on stateless widgets. "
            "ListView without itemExtent on long lists. "
            "Heavy work in build().\n"
            "DART: Null safety — ! without prior null check. Missing await on Futures. "
            "Catching dynamic exceptions instead of specific types.\n"
            "SECURITY: API keys in Dart source (use --dart-define). "
            "HTTP without certificate pinning for sensitive flows."
        ),
    },

    # ── Streaming ──────────────────────────────────────────────────────────────
    {
        "id": "kafka-java-consumer",
        "label": "Kafka Consumer / Producer (Java)",
        "category": "Streaming",
        "language": "Java",
        "framework": "Spring Kafka / Kafka Streams",
        "build_tool": "Maven / Gradle",
        "test_framework": "JUnit 5 + Embedded Kafka",
        "deploy_target": "Kubernetes / ECS",
        "build_command": "mvn clean package -DskipTests",
        "test_command": "mvn test",
        "dev_command": "",
        "dev_port": 0,
        "e2e_strategy": "jest-only",
        "mcp_servers": {
            "implement": ["github"],
            "review":    ["github"],
            "e2e":       [],
        },
        "conventions": {
            "consumers":  "src/main/java/**/consumer/",
            "producers":  "src/main/java/**/producer/",
            "processors": "src/main/java/**/processor/",
            "config":     "src/main/java/**/config/",
            "tests":      "src/test/java/",
        },
        "detect_files": ["pom.xml", "src/main/resources/application.yml"],
        "review_rules": (
            "CONSUMER (data loss risk): Blocking I/O in @KafkaListener — freezes thread. "
            "Offset committed BEFORE processing — data loss on crash. "
            "No idempotency — duplicates guaranteed on restart. "
            "No DLQ for poison messages — one bad message blocks partition forever.\n"
            "RELIABILITY: Consumer group ID not set explicitly. "
            "max.poll.records and session.timeout not tuned together. "
            "Schema compatibility mode not declared (Avro/Protobuf).\n"
            "TESTING: Not using @EmbeddedKafka."
        ),
    },
    {
        "id": "kafka-python-consumer",
        "label": "Kafka Consumer / Producer (Python)",
        "category": "Streaming",
        "language": "Python",
        "framework": "confluent-kafka / aiokafka",
        "build_tool": "pip",
        "test_framework": "pytest",
        "deploy_target": "Kubernetes / ECS",
        "build_command": "pip install -r requirements.txt",
        "test_command": "pytest",
        "dev_command": "",
        "dev_port": 0,
        "e2e_strategy": "jest-only",
        "mcp_servers": {
            "implement": ["github"],
            "review":    ["github"],
            "e2e":       [],
        },
        "conventions": {
            "consumers": "src/consumers/ or consumers/",
            "producers": "src/producers/ or producers/",
            "schemas":   "schemas/ (Avro/Protobuf)",
            "tests":     "tests/",
        },
        "detect_files": ["requirements.txt", "consumer.py", "producer.py"],
        "review_rules": (
            "CONSUMER: Sync processing in async loop blocks poll loop. "
            "Manual commit before processing completes. No idempotency.\n"
            "RELIABILITY: Exceptions in consumer loop crash process silently. "
            "No DLQ for unprocessable messages. "
            "Producer not flushing on shutdown (in-buffer messages lost).\n"
            "ASYNC (aiokafka): Blocking calls inside async coroutine. "
            "Missing consumer.stop() in finally block."
        ),
    },

    # ── Infrastructure ──────────────────────────────────────────────────────────
    {
        "id": "terraform-aws",
        "label": "Terraform (AWS)",
        "category": "Infrastructure",
        "language": "HCL",
        "framework": "Terraform",
        "build_tool": "Terraform CLI",
        "test_framework": "Terratest / tflint",
        "deploy_target": "AWS",
        "build_command": "terraform init && terraform plan",
        "test_command": "terratest ./...",
        "dev_command": "terraform plan",
        "dev_port": 0,
        "e2e_strategy": "jest-only",
        "mcp_servers": {
            "implement": ["github", "aws-mcp"],
            "review":    ["github"],
            "e2e":       [],
        },
        "conventions": {
            "modules":   "modules/",
            "envs":      "environments/ or env/",
            "variables": "variables.tf",
            "outputs":   "outputs.tf",
        },
        "detect_files": ["main.tf", "terraform.tf", ".terraform/"],
        "review_rules": (
            "SECURITY: S3 bucket public ACL or missing block_public_access. "
            "Security groups with 0.0.0.0/0 on non-HTTP ports. "
            "IAM Action '*' or Resource '*'. Secrets in .tfvars committed to git. "
            "Missing KMS encryption on RDS/S3/EBS/SQS.\n"
            "STATE: Local state (must use S3 + DynamoDB locking). Missing state encryption.\n"
            "RELIABILITY: Missing lifecycle prevent_destroy on stateful resources. "
            "No tagging strategy. Hard-coded AMI IDs.\n"
            "COST: Missing deletion_protection on production databases."
        ),
    },
    {
        "id": "aws-cdk",
        "label": "AWS CDK",
        "category": "Infrastructure",
        "language": "TypeScript / Python",
        "framework": "AWS CDK",
        "build_tool": "npm + CDK CLI",
        "test_framework": "Jest",
        "deploy_target": "AWS",
        "build_command": "npm run build && cdk synth",
        "test_command": "npm test",
        "dev_command": "cdk synth",
        "dev_port": 0,
        "e2e_strategy": "jest-only",
        "mcp_servers": {
            "implement": ["github", "aws-mcp"],
            "review":    ["github"],
            "e2e":       [],
        },
        "conventions": {
            "stacks":     "lib/",
            "constructs": "lib/constructs/",
            "tests":      "test/",
        },
        "detect_files": ["cdk.json", "cdk.out/"],
        "review_rules": (
            "SECURITY: S3 without BlockPublicAccess.BLOCK_ALL. "
            "IAM with actions ['*'] or resources ['*']. Missing encryption on EBS/RDS/SQS. "
            "Secrets in CDK code instead of SecretsManager.\n"
            "CONSTRUCTS: L1 (Cfn*) where L2 exists. "
            "Missing removal policy on stateful resources (default RETAIN must be explicit).\n"
            "TESTING: Missing Template.fromStack().hasResourceProperties assertions. "
            "No snapshot test for infrastructure drift."
        ),
    },

    # ── Data / ML ──────────────────────────────────────────────────────────────
    {
        "id": "python-ml-pipeline",
        "label": "Python ML / Data Pipeline",
        "category": "Data / ML",
        "language": "Python",
        "framework": "scikit-learn / PyTorch / TensorFlow / Pandas",
        "build_tool": "pip / Poetry / conda",
        "test_framework": "pytest",
        "deploy_target": "SageMaker / Vertex AI / Airflow",
        "build_command": "pip install -r requirements.txt",
        "test_command": "pytest",
        "dev_command": "jupyter lab",
        "dev_port": 8888,
        "e2e_strategy": "jest-only",
        "mcp_servers": {
            "implement": ["github"],
            "review":    ["github"],
            "e2e":       [],
        },
        "conventions": {
            "pipelines": "pipelines/ or src/pipelines/",
            "models":    "models/ or src/models/",
            "features":  "features/ or src/features/",
            "notebooks": "notebooks/",
            "tests":     "tests/",
        },
        "detect_files": ["requirements.txt", "setup.py", "Pipfile", "notebooks/"],
        "review_rules": (
            "DATA QUALITY: Missing schema validation and null handling. "
            "Data leakage — test set used in feature engineering. "
            "No stratified split for imbalanced classification.\n"
            "MODEL: Hardcoded hyperparameters (must be in config). "
            "No reproducibility — missing random_state/seed. "
            "Model artifacts not versioned (use MLflow/W&B).\n"
            "PIPELINE: Notebook code in production. Missing data lineage tracking. "
            "No data drift monitoring plan.\n"
            "SECURITY: Hardcoded cloud credentials. PII in training sets without anonymization."
        ),
    },
]

# ── Profile resolution (inheritance) ─────────────────────────────────────────

def resolve_profile(profile_id: str, extra_profiles: list[dict] | None = None) -> dict | None:
    """Merge a profile with its parent. Child fields override parent fields.
    Dicts (conventions, mcp_servers) are merged shallowly — child keys win.
    """
    all_p = TECH_PROFILES + (extra_profiles or [])
    by_id = {p["id"]: p for p in all_p}

    profile = by_id.get(profile_id)
    if not profile:
        return None

    parent_id = profile.get("extends")
    if not parent_id or parent_id not in by_id:
        return dict(profile)

    parent = by_id[parent_id]
    merged = dict(parent)
    merged.pop("extends", None)

    for key, val in profile.items():
        if key == "extends":
            continue
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **val}
        else:
            merged[key] = val

    # Preserve the extends field for display purposes
    merged["extends"] = parent_id
    return merged


# ── Custom profile CRUD ────────────────────────────────────────────────────────

class CustomProfileCreate(BaseModel):
    id: str
    label: str
    extends: str = ""
    category: str = "Custom"
    language: str = ""
    framework: str = ""
    build_tool: str = ""
    test_framework: str = ""
    deploy_target: str = ""
    build_command: str = ""
    test_command: str = ""
    dev_command: str = ""
    dev_port: int = 0
    e2e_strategy: str = "playwright"
    conventions: dict = Field(default_factory=dict)
    detect_files: list[str] = Field(default_factory=list)
    review_rules: str = ""
    mcp_servers: dict = Field(default_factory=dict)


async def _load_custom_profiles() -> list[dict]:
    if not _redis:
        return []
    ids = await _redis.smembers("profile:custom:index")
    result = []
    for pid in ids:
        raw = await _redis.get(f"profile:custom:{pid}")
        if raw:
            result.append(json.loads(raw))
    return result


# ── Routes ────────────────────────────────────────────────────────────────────
# Order matters: literal paths before /{profile_id}

@router.post("/custom", status_code=201)
async def create_custom_profile(req: CustomProfileCreate):
    if not _redis:
        raise HTTPException(status_code=503, detail="Redis not available")
    # Validate ID is unique (doesn't clash with built-ins)
    if any(p["id"] == req.id for p in TECH_PROFILES):
        raise HTTPException(status_code=409, detail=f"Profile ID '{req.id}' already exists as a built-in profile")
    if req.extends and not resolve_profile(req.extends):
        raise HTTPException(status_code=400, detail=f"Parent profile '{req.extends}' not found")
    payload = req.model_dump()
    await _redis.set(f"profile:custom:{req.id}", json.dumps(payload))
    await _redis.sadd("profile:custom:index", req.id)
    return payload


@router.get("/custom")
async def list_custom_profiles():
    return await _load_custom_profiles()


@router.delete("/custom/{profile_id}", status_code=204)
async def delete_custom_profile(profile_id: str):
    if not _redis:
        raise HTTPException(status_code=503, detail="Redis not available")
    await _redis.delete(f"profile:custom:{profile_id}")
    await _redis.srem("profile:custom:index", profile_id)


@router.get("")
async def list_profiles():
    """All profiles (built-in + custom), resolved."""
    custom = await _load_custom_profiles()
    all_profiles = TECH_PROFILES + custom
    return [resolve_profile(p["id"], custom) or p for p in all_profiles]


@router.get("/categories")
async def list_categories():
    """All profiles grouped by category, resolved."""
    custom = await _load_custom_profiles()
    all_profiles = TECH_PROFILES + custom
    resolved = [resolve_profile(p["id"], custom) or p for p in all_profiles]
    grouped: dict[str, list] = {}
    for p in resolved:
        grouped.setdefault(p["category"], []).append(p)
    return grouped


@router.get("/{profile_id}")
async def get_profile(profile_id: str):
    """Single resolved profile (inheritance merged in)."""
    custom = await _load_custom_profiles()
    resolved = resolve_profile(profile_id, custom)
    if not resolved:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found")
    return resolved
