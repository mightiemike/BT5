### Title
`BlockingCheckClient` unconditionally disables TLS certificate validation (`danger_accept_invalid_certs(true)`), enabling MITM on the external transaction-admission control channel — (`File: crates/starknet_transaction_prover/src/blocking_check.rs`)

### Summary

The `BlockingCheckClient` in the Starknet transaction prover builds its `reqwest` HTTP client with `.danger_accept_invalid_certs(true)` hardcoded and unconditional. This disables all TLS certificate validation for every connection to the external blocking check service, regardless of whether the configured URL uses `http://` or `https://`. Separately, neither `blocking_check_url` nor `rpc_node_url` are validated for their URL scheme during config construction. A network-adjacent attacker can perform a man-in-the-middle attack to intercept and modify blocking check responses, causing the prover to generate proofs for transactions that should be blocked, or to deny proofs for valid transactions.

### Finding Description

In `BlockingCheckClient::new`, the `reqwest` client is constructed as:

```rust
let http_client = reqwest::Client::builder()
    .danger_accept_invalid_certs(true)
    .build()
    .expect("Failed to build blocking check HTTP client");
``` [1](#0-0) 

This call disables **all** TLS certificate validation — not merely self-signed certificate validation. The comment says "configured to accept self-signed TLS certificates," but `danger_accept_invalid_certs(true)` also accepts certificates with wrong hostnames, expired certificates, and certificates signed by any arbitrary CA, including an attacker's. The flag is applied unconditionally to every `blocking_check_url` regardless of scheme or host.

The config validation in `ServiceConfig::from_args` only checks that `blocking_check_url` is a syntactically parseable URL:

```rust
if let Some(url_str) = &config.blocking_check_url {
    url::Url::parse(url_str).map_err(|e| {
        ConfigError::InvalidArgument(format!("Invalid blocking_check_url: {e}"))
    })?;
}
``` [2](#0-1) 

There is no check that the scheme is `https://`, no check that the host is local, and no warning when `http://` is used with an external host. The same absence of scheme validation applies to `rpc_node_url`, which is only checked for non-emptiness: [3](#0-2) 

The CLI documentation for `blocking_check_url` says "HTTPS with self-signed cert supported," implying HTTPS is the intended transport, but neither the config layer nor the client constructor enforces this: [4](#0-3) 

The `blocking_check_url` is stored in `ProverConfig` as a plain `String` with no scheme constraint: [5](#0-4) 

At prover construction time, the URL is parsed and passed directly to `BlockingCheckClient::new` with no scheme gate: [6](#0-5) 

### Impact Explanation

The blocking check service is the admission control gate for the transaction prover. Its response determines whether a transaction receives a proof or is rejected as blocked: [7](#0-6) 

A MITM attacker who intercepts the connection to the blocking check service can:

1. **Return `Allowed` for every request** — transactions that the operator's blocking service would reject (e.g., sanctioned addresses, policy-violating transactions) receive proofs. This directly maps to the **High** impact: "Mempool/gateway/RPC admission accepts invalid transactions … before sequencing."
2. **Return `Blocked` for every request** — all proving requests are denied, a complete DoS of the prover.
3. **Inject arbitrary `additional_data`** — the prover relays the `additional_data` object from the blocking check response verbatim in the prove response without interpreting it: [8](#0-7) 

Because `danger_accept_invalid_certs(true)` is unconditional, even an operator who correctly configures `https://` is fully exposed: the attacker presents any certificate and it is accepted.

### Likelihood Explanation

- **Trigger**: The operator must configure `blocking_check_url` (optional, `None` by default). Once configured, the vulnerability is always active.
- **Attacker position**: Network-adjacent (same network segment, ISP, or cloud VPC). No privileged access to the prover host is required.
- **Exploitation**: Straightforward — standard MITM tooling (e.g., `mitmproxy`) suffices. The attacker presents any TLS certificate; `danger_accept_invalid_certs(true)` ensures it is accepted. For `http://` URLs, no TLS interception is needed at all.
- **Detection**: None — the prover logs only warn-level messages on network errors, not on successful (but attacker-controlled) responses.

### Recommendation

1. **Remove `danger_accept_invalid_certs(true)`** from `BlockingCheckClient::new`. If self-signed certificates must be supported, add an explicit opt-in config field (e.g., `blocking_check_accept_invalid_certs: bool`, defaulting to `false`) and document the risk.
2. **Add scheme validation** in `ServiceConfig::from_args` for both `blocking_check_url` and `rpc_node_url`: reject `http://` URLs whose host is not a loopback address (`127.0.0.1`, `::1`, `localhost`), or at minimum emit a prominent warning.
3. **Mirror the pattern already used for CORS origins** — `normalize_cors_allow_origin` already enforces `http`/`https` and validates the host; apply equivalent logic to outbound RPC URLs. [9](#0-8) 

### Proof of Concept

**Scenario A — `http://` URL (plaintext MITM):**

```bash
# Operator configures the prover with an external http:// blocking check URL
BLOCKING_CHECK_URL=http://external-check-service.example.com/ \
  starknet-transaction-prover --rpc-url https://node.example.com/rpc/v0_10

# Attacker on the same network runs mitmproxy and intercepts the plaintext HTTP POST
# to starknet_checkTransaction, replacing the response body with:
# {"jsonrpc":"2.0","result":{},"id":1}
# => BlockingCheckResult::Allowed — every transaction gets a proof regardless of policy
```

**Scenario B — `https://` URL with forged certificate (TLS MITM):**

```bash
# Operator configures https:// — believes the connection is secure
BLOCKING_CHECK_URL=https://external-check-service.example.com/ \
  starknet-transaction-prover --rpc-url https://node.example.com/rpc/v0_10

# Attacker performs ARP spoofing / DNS poisoning, presents a self-signed certificate
# for external-check-service.example.com.
# danger_accept_invalid_certs(true) causes the prover to accept it without error.
# Attacker returns {"jsonrpc":"2.0","result":{},"id":1} for all requests.
# => All transactions pass the blocking check and receive proofs.
```

The config validation path that should catch this — but does not — is: [2](#0-1)

### Citations

**File:** crates/starknet_transaction_prover/src/blocking_check.rs (L90-95)
```rust
    pub(crate) fn new(url: Url, timeout_millis: u64, fail_open: bool) -> Self {
        let http_client = reqwest::Client::builder()
            .danger_accept_invalid_certs(true)
            .build()
            .expect("Failed to build blocking check HTTP client");
        Self { http_client, url, timeout_millis, fail_open }
```

**File:** crates/starknet_transaction_prover/src/blocking_check.rs (L145-148)
```rust
        match json_rpc_response.error {
            None => BlockingCheckResult::Allowed(
                json_rpc_response.result.and_then(|check_result| check_result.additional_data),
            ),
```

**File:** crates/starknet_transaction_prover/src/server/config.rs (L353-359)
```rust
        // Validate blocking check URL early so an invalid value surfaces as a clean config error
        // instead of a panic at prover construction time.
        if let Some(url_str) = &config.blocking_check_url {
            url::Url::parse(url_str).map_err(|e| {
                ConfigError::InvalidArgument(format!("Invalid blocking_check_url: {e}"))
            })?;
        }
```

**File:** crates/starknet_transaction_prover/src/server/config.rs (L384-389)
```rust
        // Validate required fields.
        if config.rpc_node_url.is_empty() {
            return Err(ConfigError::MissingRequiredField(
                "rpc_node_url is required (provide via --rpc-url or config file)".to_string(),
            ));
        }
```

**File:** crates/starknet_transaction_prover/src/server/config.rs (L583-586)
```rust
    /// URL of the external blocking check JSON-RPC service (HTTPS with self-signed cert
    /// supported).
    #[arg(long, value_name = "URL", env = "BLOCKING_CHECK_URL")]
    pub blocking_check_url: Option<String>,
```

**File:** crates/starknet_transaction_prover/src/config.rs (L26-28)
```rust
    /// URL of the external blocking check JSON-RPC service. `None` disables the feature.
    pub blocking_check_url: Option<String>,
    /// Milliseconds to wait for the blocking check response before applying the
```

**File:** crates/starknet_transaction_prover/src/proving/virtual_snos_prover.rs (L90-97)
```rust
        let blocking_check_client = prover_config.blocking_check_url.as_ref().map(|url_str| {
            let url = Url::parse(url_str).expect("Invalid blocking_check_url in config");
            BlockingCheckClient::new(
                url,
                prover_config.blocking_check_timeout_millis,
                prover_config.blocking_check_fail_open,
            )
        });
```

**File:** crates/starknet_transaction_prover/src/proving/virtual_snos_prover.rs (L173-178)
```rust
        let result = match &self.blocking_check_client {
            None => self.run_and_prove(block_id, vec![invoke_tx]).await?,
            Some(client) => {
                self.prove_with_blocking_check(client, block_id, transaction, invoke_tx).await?
            }
        };
```

**File:** crates/starknet_transaction_prover/src/server/cors.rs (L85-90)
```rust
    if !matches!(parsed.scheme(), "http" | "https") {
        return Err(ConfigError::InvalidArgument(format!(
            "Invalid cors_allow_origin '{cors_allow_origin}': only http:// and https:// are \
             supported."
        )));
    }
```
