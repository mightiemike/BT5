### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender` (the `msg.sender` of the `pool.swap()` call) against the per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks the **router's** allowlist status, not the **user's**. If the pool admin allowlists the router so that legitimate users can reach the pool through the supported periphery path, every non-allowlisted user can bypass the curation gate by routing through the same router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol – swap()
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is on the allowlist keyed by the calling pool (`msg.sender` inside the extension = the pool):

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` with itself as `msg.sender`:

```solidity
// MetricOmmSimpleRouter.sol – exactInputSingle()
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

The pool therefore passes `sender = address(router)` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Consequence:** A pool admin who wants legitimate users to be able to use the router must allowlist the router address. Once the router is allowlisted, every address on the network can call `router.exactInputSingle` and have the extension approve the swap, because the extension only sees the router — not the caller.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise vetted addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps against the pool's liquidity, draining LP value or violating regulatory/operational constraints the pool admin intended to enforce. This is a direct, complete bypass of the configured access-control guard with no additional privilege required beyond calling a public periphery contract.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entry point for end users. Any pool admin who configures `SwapAllowlistExtension` and also allowlists the router (the natural operational choice to make the pool usable through the standard UI) immediately opens the bypass to all users. The attacker needs no special role, no flash loan, and no oracle manipulation — only a standard router call.

---

### Recommendation

The extension must gate on the **economic actor** (the human or contract that controls the funds and benefits from the trade), not on the intermediary router. Two complementary fixes:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the router to be trusted to supply the correct identity, which is acceptable given it is a protocol-controlled contract.

2. **Add a `payer` / `originator` field to the `beforeSwap` hook interface:** Extend `IMetricOmmExtensions.beforeSwap` with an explicit `originator` address that the pool populates from a transient-storage context set by the router before calling `pool.swap`. The extension then checks `originator` instead of `sender`.

Until fixed, pool admins should **not** allowlist the router address on pools that use `SwapAllowlistExtension`; instead they should require users to call `pool.swap` directly.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` as `EXTENSION_1` and `BEFORE_SWAP_ORDER` pointing to it.
2. Admin calls `extension.setAllowedToSwap(pool, alice, true)` — Alice is KYC'd.
3. Admin calls `extension.setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use the UI.
4. Bob (not KYC'd, not individually allowlisted) calls:
   ```solidity
   router.exactInputSingle(ExactInputSingleParams({
       pool: pool,
       recipient: bob,
       zeroForOne: true,
       amountIn: 1_000e18,
       amountOutMinimum: 0,
       priceLimitX64: 0,
       tokenIn: token0,
       extensionData: "",
       deadline: block.timestamp
   }));
   ```
5. Router calls `pool.swap(bob, true, ..., "")` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob receives token1 output; the allowlist guard was never applied to Bob's identity.

**Corrupted invariant:** `allowedSwapper[pool][bob]` is `false`, yet Bob's swap settles successfully. The extension's per-pool curation policy is silently voided for all router-mediated swaps. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L229-240)
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
