Let me read the router and liquidity adder to understand how `sender` and `owner` are forwarded.

### Title
`SwapAllowlistExtension.beforeSwap` Checks the Router Address Instead of the End User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. A pool admin who wants to support router-based swaps must therefore allowlist the router address. Once the router is allowlisted, every user—including those the admin explicitly excluded—can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the end user
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

`SwapAllowlistExtension.beforeSwap` then checks that `sender` (the direct pool caller) is allowlisted for the calling pool (`msg.sender` inside the extension = pool):

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When `MetricOmmSimpleRouter` calls `pool.swap()`, `sender` inside the extension is the router's address, not the end user's address. The pool admin has two choices:

1. **Do not allowlist the router** → all router-mediated swaps revert, even for allowlisted users. Allowlisted users can only swap by calling the pool directly.
2. **Allowlist the router** → the check becomes `allowedSwapper[pool][router] == true`, which passes for every caller of the router, including users the admin explicitly excluded from the allowlist.

There is no third option. The extension provides no mechanism to check the original end user when the swap arrives through the router. A disallowed user who cannot call `pool.swap()` directly can always route through `MetricOmmSimpleRouter` once the router is allowlisted.

The analog to the external report's bug class is exact: the allowlist was configured against specific user addresses (the "snapshot" of intended gating), but at execution time the hook evaluates the router address (a different actor) instead of the live end user, causing the configured guard to be silently bypassed.

---

### Impact Explanation

A curated pool that uses `SwapAllowlistExtension` to restrict trading to a specific set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The disallowed user can execute swaps at live oracle prices, draining pool liquidity or extracting value that the allowlist was designed to prevent. Because the pool's LP positions are directly exposed to every swap, this is a direct loss of LP principal and protocol fees above Sherlock thresholds.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point documented in the periphery. Any pool admin who deploys a curated pool and also wants to support the standard router UX will naturally allowlist the router. The bypass requires no special privileges, no non-standard tokens, and no malicious setup—only a standard router call from any EOA.

---

### Recommendation

The extension must recover the original end user's address rather than trusting the `sender` argument when the direct caller is a known intermediary. Two concrete approaches:

1. **Pass the end user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted to encode honestly, which is acceptable for a first-party periphery contract.

2. **Check `sender` and fall back to a forwarded-user field**: Add a `forwardedSender` concept to the extension interface so the router can attest the real user, and the extension checks `allowedSwapper[pool][forwardedSender]` when `sender` is a known router.

Until fixed, pool admins should be warned that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)`.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only allowed swapper.
3. Admin calls `setAllowedToSwap(pool, router, true)` — to let Alice use the standard router UI.
4. Bob (explicitly not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The router calls `pool.swap(recipient=Bob, ...)` with `msg.sender = router`.
6. `_beforeSwap` is called with `sender = router`.
7. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes at live oracle prices. The allowlist is bypassed. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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
