Now I have all the information needed. Let me trace the exact call path for the swap allowlist bypass.

### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any unprivileged caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router, not the end-user. If the pool admin allowlists the router (the only way to let allowlisted users trade through the router), every unprivileged user can bypass the allowlist by calling through the same public router.

---

### Finding Description

**Pool → Extension argument binding**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every before-swap extension:

```solidity
// MetricOmmPool.sol line 231
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the first argument to every configured extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
``` [2](#0-1) 

**SwapAllowlistExtension identity check**

The extension gates on `sender` keyed by `msg.sender` (the pool):

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

**Router call path**

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly, making the router itself `msg.sender` at the pool:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [4](#0-3) 

The original end-user's address (`msg.sender` of the router call) is stored only in transient callback context for payment purposes; it is never forwarded to the pool or the extension.

**The impossible dilemma**

For the allowlist to function with the router, the pool admin must choose one of two broken states:

| Admin choice | Consequence |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all — broken UX |
| **Allowlist the router** | Every unprivileged user can bypass the allowlist by calling through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

Any user who is **not** on the allowlist can execute swaps on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point). The extension sees `sender = router`, which the pool admin must have allowlisted to support legitimate router-mediated swaps. The allowlist — the sole access-control mechanism for swap gating on these pools — is fully bypassed. This is a direct admin-boundary break: an unprivileged path (the public router) defeats a configured pool-level guard, allowing unauthorized parties to trade on pools intended for restricted audiences (e.g., KYC-gated, institutional-only, or regulatory-compliant pools).

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap interface; pool admins who want allowlisted users to trade through it **must** allowlist the router.
- The router is a public, permissionless contract — any address can call it.
- No special knowledge, capital, or timing is required; a single `exactInputSingle` call suffices.
- The bypass is deterministic and repeatable on every block.

---

### Recommendation

The extension must gate on the **economic actor** (the end-user), not the intermediary. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a coordinated convention between the router and the extension.

2. **Check `sender` only for direct pool calls; require a signed proof for router calls**: The extension can distinguish direct calls (where `sender` is the real user) from router calls by checking whether `sender` is a known router and then requiring an allowlisted address to be proven via a signature or a trusted forwarder pattern embedded in `extensionData`.

3. **Separate allowlist entries for routers vs. end-users**: Document clearly that allowlisting the router opens the pool to all users, and provide a separate "router-aware" extension that decodes the real payer from transient or calldata context.

---

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  allowedUser A is allowlisted: allowedSwapper[P][A] = true
  Pool admin allowlists the router R so A can trade via router:
    allowedSwapper[P][R] = true

Attack (single transaction, no special privileges):
  disallowedUser D calls:
    router.exactInputSingle({
        pool: P,
        zeroForOne: true,
        amountIn: X,
        recipient: D,
        ...
    })

  Call chain:
    D → router.exactInputSingle()
        router → pool.swap(recipient=D, ...)   [msg.sender = router R]
            pool → extension.beforeSwap(sender=R, ...)
                check: allowedSwapper[P][R] == true  ✓  (passes)
            pool executes swap, sends tokens to D

Result:
  D successfully swaps on the allowlisted pool.
  The allowlist never checked D's address.
``` [3](#0-2) [1](#0-0) [4](#0-3)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```
