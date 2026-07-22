### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Enabling Complete Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When `MetricOmmSimpleRouter` intermediates a swap, `sender` is the router's address, not the end-user's. A pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously opens the gate to every user on-chain, completely defeating the allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which the pool sets to its own `msg.sender` — i.e., whoever called `pool.swap()`. [2](#0-1) 

When a user routes through `MetricOmmSimpleRouter`, the call chain is:

```
user → MetricOmmSimpleRouter.exactInput*()
           → pool.swap(recipient, ...)   ← msg.sender = router
               → _beforeSwap(msg.sender=router, ...)
                   → extension.beforeSwap(sender=router, ...)
```

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. [3](#0-2) 

A pool admin who wants allowlisted users to be able to use the router **must** add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once that entry exists, every user on-chain can call the router and pass the extension check, because the extension only sees the router address.

The admin faces an impossible choice:
- **Do not allowlist the router** → allowlisted users cannot use the router (core swap path broken for the intended audience).
- **Allowlist the router** → the allowlist is completely bypassed by any user. [4](#0-3) 

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting who may swap on a pool. A complete bypass means any address can execute swaps on a pool that the operator intended to be permissioned (e.g., KYC-gated, institutional-only, or whitelist-only pools). Depending on pool configuration this can result in:

- Unauthorized users draining one-sided liquidity at oracle price.
- Protocol-fee revenue accruing from trades the operator explicitly prohibited.
- Regulatory or compliance failure for pools that rely on the allowlist as an access control boundary.

This is a broken core pool functionality / admin-boundary break with direct fund-impacting consequences.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point documented in the protocol. Any pool that deploys `SwapAllowlistExtension` and expects users to interact via the router will encounter this issue. The trigger requires no special privilege — any unprivileged address can call the router. The only precondition is that the pool admin has allowlisted the router (the expected operational setup).

---

### Recommendation

The extension must gate the **end-user identity**, not the intermediary. Two sound approaches:

1. **Pass the original caller through `extensionData`**: The router encodes the actual user address in `extensionData`; the extension decodes and checks it. This requires the extension to trust the router's encoding, so the router address itself should also be verified.

2. **Add a `swapper` parameter to the pool's `swap()` interface**: The pool accepts an explicit `swapper` address (validated against `msg.sender` or a trusted forwarder list) and passes it as `sender` to extensions. This is the cleanest fix and mirrors how Uniswap v4 handles hook-visible originators.

Until fixed, pools using `SwapAllowlistExtension` should not allowlist the router and should require users to call `pool.swap()` directly.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` attached; `allowAllSwappers[pool] = false`.
2. Admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is permitted.
3. Admin calls `setAllowedToSwap(pool, router, true)` — necessary so `userA` can use the router.
4. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
5. Router calls `pool.swap(userB, ...)` — `msg.sender` to the pool is the router.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. `userB`'s swap executes successfully despite never being allowlisted. [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-core/contracts/MetricOmmPool.sol (L281-295)
```text
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
      extensionData
    );
```
