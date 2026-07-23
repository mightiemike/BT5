### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the end user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to restrict which addresses may swap against a pool. Its `beforeSwap` hook receives `sender` — the address that called `pool.swap()` — and checks it against a per-pool allowlist. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is always the router contract, not the end user. A pool admin who allowlists the router (the only way to enable router-mediated swaps on an allowlisted pool) inadvertently grants every user on the router unrestricted swap access, defeating the allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol  line 230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    zeroForOne,
    amountSpecified,
    priceLimitX64,
    packedSlot0Initial,
    bidPriceX64,
    askPriceX64,
    extensionData
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that identity against the allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [2](#0-1) 

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter` is the intermediary, `sender` = router address for every user, regardless of who initiated the transaction.

The allowlist maps `allowedSwapper[pool][swapper]`. For any router-mediated swap to succeed, the pool admin must add the router to this map. The moment the router is allowlisted, the check `allowedSwapper[pool][router]` returns `true` for every user who routes through it — the per-user gate collapses to a per-contract gate.

The `ExtensionCalling._beforeSwap` dispatcher passes `sender` through without modification:

```solidity
// metric-core/contracts/ExtensionCalling.sol  line 149-177
function _beforeSwap(
    address sender,
    ...
) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
    );
}
``` [3](#0-2) 

There is no mechanism for the router to forward the originating user's address into `sender`; `extensionData` is user-controlled bytes and cannot be trusted for identity.

---

### Impact Explanation

Any user can bypass a pool's swap allowlist by routing through `MetricOmmSimpleRouter`. Once the router is allowlisted (required for router-mediated swaps to function), the allowlist no longer gates individual users — it gates only the router contract. Unauthorized users gain full swap access to a pool that was intended to be restricted, enabling them to drain liquidity, extract fees, or execute swaps the pool admin explicitly prohibited. This is a direct loss of the pool's intended access-control invariant with fund-impacting consequences (unauthorized swap settlement against restricted LP capital).

---

### Likelihood Explanation

The scenario is reachable by any unprivileged user with no special setup:
1. A pool is deployed with `SwapAllowlistExtension` configured.
2. The pool admin allowlists the router (a natural operational step to support normal UX).
3. Any user calls `MetricOmmSimpleRouter.exactInput/exactOutput` targeting that pool.
4. The extension sees `sender` = router address, passes the check, and the swap executes.

No malicious initial setup, non-standard tokens, or privileged cooperation is required beyond the pool admin performing the expected operational action of enabling router access.

---

### Recommendation

The `sender` identity passed to extension hooks must reflect the economic actor, not the intermediary contract. Two complementary fixes:

1. **Router-level**: `MetricOmmSimpleRouter` should encode the originating `msg.sender` into `extensionData` using a signed or authenticated field, and `SwapAllowlistExtension` should decode and verify it when present.
2. **Extension-level**: `SwapAllowlistExtension` should expose a separate allowlist for trusted forwarders (e.g., the router) and, when `sender` is a known forwarder, decode the real user from `extensionData` before performing the allowlist check.

The simplest safe default is to never allowlist the router address directly; instead, require that all allowlisted swaps arrive via direct `pool.swap()` calls, and document this constraint explicitly.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension (beforeSwap order set)
  allowedSwapper[pool][router] = true   ← admin enables router access
  allowedSwapper[pool][attacker] = false ← attacker is explicitly blocked

Attack:
  attacker calls MetricOmmSimpleRouter.exactInput(pool, ...)
    → router calls pool.swap(recipient=attacker, ...)
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
        allowedSwapper[pool][router] == true  ✓ passes
    → swap executes, attacker receives tokens

Result:
  Attacker swaps against a pool they are explicitly blocked from,
  receiving output tokens while the pool's LP capital is consumed
  without the access restriction the admin configured.
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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
