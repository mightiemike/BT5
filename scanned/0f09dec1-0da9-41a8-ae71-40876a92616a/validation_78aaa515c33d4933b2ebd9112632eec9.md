### Title
`SwapAllowlistExtension.beforeSwap` Gates the Router Address Instead of the Real User When Swaps Are Routed Through `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is the production guard that curated pools use to restrict which addresses may swap. Its `beforeSwap` hook checks the `sender` argument passed by the pool. The pool always passes its own `msg.sender` as `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. If the pool admin allowlists the router (the only way to enable router-mediated swaps), every user on-chain can bypass the allowlist by routing through it.

---

### Finding Description

**Pool `swap` passes `msg.sender` as `sender` to the extension:** [1](#0-0) 

```solidity
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
```

**`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:** [2](#0-1) 

**`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[pool][sender]`:** [3](#0-2) 

```solidity
function beforeSwap(address sender, address, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

When a user calls `MetricOmmSimpleRouter.exactInput*()`, the router calls `pool.swap(...)`. Inside `pool.swap`, `msg.sender` is the **router**, so `sender` forwarded to the extension is the **router address**, not the end user.

This creates an inescapable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Individually allowlisted users cannot swap through the standard periphery at all |
| **Allowlist the router** | Every user on-chain can bypass the allowlist by routing through the router |

The second branch is the critical one: allowlisting the router is the only way to make router-mediated swaps work, but doing so opens the curated pool to all users, completely defeating the guard.

The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position holder), which the pool passes correctly regardless of who the payer/caller is: [4](#0-3) 

The swap path has no equivalent correct binding.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a whitelist of addresses is rendered fully open to any user who routes through `MetricOmmSimpleRouter`. The attacker receives real token output from the pool's LP reserves without being on the allowlist. This is a direct loss of curation policy and, in pools where the allowlist is used to prevent adverse selection or enforce KYC/regulatory constraints, constitutes a high-severity policy bypass with direct fund-impacting consequences for LPs.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point in the periphery. Any user who reads the docs or inspects the periphery contracts will discover the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — only calling the public router instead of the pool directly. Likelihood is high.

---

### Recommendation

The pool should pass the **end user's identity** to the extension, not the immediate `msg.sender`. Two viable approaches:

1. **Router forwards the originating user**: `MetricOmmSimpleRouter` encodes the real user address in `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender`**: If the pool's design intent is to gate who *receives* output (i.e., who benefits from the swap), checking `recipient` is already correctly forwarded. However, this changes the semantic of the guard.

3. **Pool passes a dedicated `originator` field**: The pool could accept an explicit `originator` parameter in `swap()` and forward it to extensions, letting the router supply the real user while the pool enforces that `msg.sender` implements the callback.

The cleanest fix consistent with the existing interface is option 1: establish a convention where the router ABI-encodes the real user as the first word of `extensionData`, and `SwapAllowlistExtension` decodes it when present.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` as `extension1`, configured with `beforeSwap` order.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only allowed swapper.
3. Pool admin also calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
5. The router calls `pool.swap(recipient=bob, ...)`. Inside the pool, `msg.sender = router`.
6. `_beforeSwap(router, bob, ...)` is called. The extension checks `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes successfully despite not being on the allowlist.

Alternatively, if the admin does **not** allowlist the router:

4. Alice calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
5. The router calls `pool.swap(...)`. Extension checks `allowedSwapper[pool][router]` → `false`.
6. Reverts `NotAllowedToSwap` — Alice cannot use the standard periphery even though she is individually allowlisted. [3](#0-2) [5](#0-4) [2](#0-1)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
