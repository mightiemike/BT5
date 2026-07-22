### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, the router is the direct caller, so `sender` = router address. The extension therefore gates the router contract, not the actual end-user. This makes it structurally impossible to correctly enforce a per-user allowlist for router-mediated swaps: if the router is allowlisted, every user bypasses the allowlist; if it is not, no allowlisted user can use the router at all.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` reads:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension is called by the pool via `CallExtension.callExtension`). `sender` is the first argument forwarded by the pool from its own `swap()`:

```solidity
_beforeSwap(
    msg.sender,   // whoever called pool.swap() — the router when going through the router
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

So `sender` arriving at the extension is the router's address, not the end-user's address. The actual end-user (`msg.sender` to the router) is never forwarded to the pool or the extension.

The extension therefore evaluates `allowedSwapper[pool][router]`:

- **Router not allowlisted**: every router-mediated swap reverts with `NotAllowedToSwap`, even for users the pool admin explicitly allowlisted. Allowlisted users are forced to call `pool.swap()` directly, bypassing the standard periphery interface entirely.
- **Router allowlisted** (the only way to enable router-mediated swaps): the check passes for every caller regardless of identity, because the router is a shared public contract. Any non-allowlisted user can bypass the allowlist by routing through `MetricOmmSimpleRouter`.

The actual user's address is not present in any argument the extension receives (`sender` = router, `recipient` = output destination, `extensionData` = user-controlled bytes with no enforced identity). There is no on-chain path for the extension to recover the true initiator.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict swaps to KYC'd or otherwise vetted addresses loses that protection entirely for router-mediated flows. Any unprivileged user can call `router.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting the curated pool and the allowlist check passes as long as the router is allowlisted. The pool's LP assets are exposed to swaps from actors the pool admin explicitly intended to exclude, which can result in direct loss of LP principal through adverse selection or regulatory non-compliance leading to pool shutdown.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface documented and deployed by the protocol. Any pool admin who wants their allowlisted users to be able to swap through the standard router must allowlist the router address. Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges, no flash loan, and no multi-step setup — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must gate the actual end-user, not the intermediary contract. Two complementary fixes:

1. **Enforce user identity in `extensionData`**: Require the router to encode `msg.sender` (the actual user) into `extensionData` and have the extension decode and verify it. The pool admin would allowlist user addresses, not the router.

2. **Check `recipient` as a proxy** (partial): For single-hop exact-input swaps the recipient is often the user, but this breaks for multi-hop paths where intermediate recipients are the router itself.

The cleanest fix is option 1: define a standard `extensionData` encoding for the allowlist extension that includes the originating user address, and have the router populate it. The extension then checks `allowedSwapper[pool][decodedUser]` instead of `allowedSwapper[pool][sender]`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists router: extension.setAllowedToSwap(pool, address(router), true)
  - Pool admin does NOT allowlist attacker: allowedSwapper[pool][attacker] == false

Attack:
  1. attacker calls router.exactInputSingle({pool: curatedPool, ...})
  2. router calls pool.swap(recipient, ...) — msg.sender to pool = router
  3. pool calls _beforeSwap(msg.sender=router, ...)
  4. extension checks allowedSwapper[pool][router] == true → passes
  5. swap executes; attacker receives output tokens

Result: attacker bypasses the per-user allowlist entirely.

Alternatively, if router is NOT allowlisted:
  1. allowlisted user calls router.exactInputSingle({pool: curatedPool, ...})
  2. extension checks allowedSwapper[pool][router] == false → reverts NotAllowedToSwap
  3. allowlisted user cannot use the router at all
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
