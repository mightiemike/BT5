### Title
`SwapAllowlistExtension` checks router address instead of actual user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the end user. If the router is allowlisted (which is necessary for any allowlisted user to use the router), the allowlist is bypassed for **all** users, including those the pool admin explicitly excluded.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check against the `sender` parameter:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the caller of the extension), and `sender` is the value the pool passes as the first argument to `beforeSwap`. The pool always sets this to `msg.sender` of the `swap()` call:

```solidity
// ExtensionCalling.sol _beforeSwap
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...)
    )
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInput` (or any `exact*` variant), the router calls `pool.swap(...)` directly. At that point, `msg.sender` seen by the pool is the **router address**, so `sender` forwarded to the extension is the router, not the end user.

The pool admin faces an impossible dilemma:

| Router allowlisted? | Effect |
|---|---|
| Yes | Every user — including explicitly blocked ones — can bypass the allowlist by routing through the router |
| No | Every allowlisted user is blocked from using the router; only direct `pool.swap()` callers work |

The first case is the exploitable path: a pool admin allowlists the router so that legitimate users can use the standard periphery, but this simultaneously opens the gate to all non-allowlisted users.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` (e.g., for regulatory compliance, KYC gating, or restricting trading to specific market makers) can be fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The attacker does not need any special privilege — they simply call the public router. The pool's LP assets are exposed to swaps from actors the pool admin explicitly intended to exclude, which can result in direct loss of LP principal through adverse selection or regulatory violation.

**Severity: High** — complete allowlist bypass on a curated pool via a standard, publicly accessible periphery path.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap entry point.
- Any pool admin who deploys a `SwapAllowlistExtension` and also allowlists the router (the natural setup) triggers the bypass automatically.
- No special timing, flash loan, or privileged access is required — any EOA can call the router.
- The bypass is permanent until the router is de-allowlisted, which breaks all router-mediated swaps for legitimate users.

---

### Recommendation

The extension must check the **economically relevant actor** — the end user — not the intermediary. Two sound approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData` before calling `pool.swap()`, and the extension decodes and checks it. This requires a trusted router assumption.

2. **Check `tx.origin` as a fallback**: Only acceptable if the protocol explicitly documents and accepts `tx.origin` semantics; it breaks contract-to-contract flows.

3. **Preferred — gate by `recipient` instead of `sender`**: For swap allowlists the recipient is the economically relevant party receiving output tokens. The pool already passes `recipient` as the second argument to `beforeSwap`; the extension could check `recipient` instead of `sender` for pools where the recipient is the intended gating identity.

The analogous deposit-side extension (`DepositAllowlistExtension`) correctly checks `owner` (the position owner) rather than `sender` (the payer/router), so the fix pattern is already present in the codebase. [3](#0-2) 

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists only address(alice) via setAllowedToSwap(pool, alice, true)
  - Pool admin also allowlists the router: setAllowedToSwap(pool, router, true)
    (required so alice can use the router)

Attack:
  1. bob (not allowlisted) calls MetricOmmSimpleRouter.exactInput(pool, ...)
  2. Router calls pool.swap(recipient=bob, ...)
  3. Pool calls extension.beforeSwap(sender=router, ...)
  4. Extension checks: allowedSwapper[pool][router] == true  ✓
  5. Swap executes — bob bypassed the allowlist

Result:
  - bob receives output tokens from a pool that was supposed to exclude him
  - LP assets are exposed to an actor the pool admin explicitly blocked
``` [1](#0-0) [2](#0-1)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
