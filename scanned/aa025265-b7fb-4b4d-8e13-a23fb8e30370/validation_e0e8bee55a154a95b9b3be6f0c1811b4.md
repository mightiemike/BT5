### Title
`SwapAllowlistExtension` checks the router address instead of the originating user, allowing any unprivileged caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool, which equals `msg.sender` of the `pool.swap(...)` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. Any pool admin who allowlists the router (the natural step to let their approved users access the router UX) simultaneously grants every unprivileged user the ability to bypass the allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
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

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is allowlisted for the calling pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap(...)` directly, making the router the pool's `msg.sender`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
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

The router is a public, permissionless contract. When the pool admin allowlists the router address (the only way to let their approved users access the router UX), the extension check becomes `allowedSwapper[pool][router]`, which passes for **every** caller of the router, not just the approved ones.

---

### Impact Explanation

Any user who is **not** on the allowlist can execute swaps against a restricted pool by routing through `MetricOmmSimpleRouter`. The allowlist invariant — "only approved addresses may trade against this pool" — is completely broken for router-mediated paths. Consequences include:

- Unauthorized extraction of liquidity from pools intended for permissioned counterparties.
- Unauthorized price impact on pools whose LP positions are protected by the allowlist.
- Pool insolvency risk if the restricted pool was designed to absorb only trusted, bounded order flow.

---

### Likelihood Explanation

The pool admin must allowlist the router to give their approved users a normal swap UX (slippage protection, multi-hop, ETH wrapping). This is the expected operational step. Once the router is allowlisted, the bypass is trivially reachable by any unprivileged user with no special setup. The router is deployed and public on every supported chain.

---

### Recommendation

The extension must check the **originating user**, not the intermediate router. Two complementary fixes:

1. **Pass the original user through the router.** The router already stores `msg.sender` in transient context (`_setNextCallbackContext(..., msg.sender, ...)`). The pool could expose a way for extensions to read this, or the router could pass the original caller as `extensionData` for the extension to decode.

2. **Check `sender` only when it is not a known router; otherwise check the original user from `extensionData`.** The extension can require that router-mediated calls include the original caller's address in `extensionData` and verify it against the allowlist.

The simplest safe fix: require that `extensionData` always carries the original caller's address when routing through any intermediary, and have `SwapAllowlistExtension` check that address instead of `sender` when `sender` is a registered router.

---

### Proof of Concept

```solidity
// 1. Deploy pool with SwapAllowlistExtension configured.
// 2. Pool admin allowlists Alice and the router (so Alice can use the router UX):
extension.setAllowedToSwap(address(pool), alice, true);
extension.setAllowedToSwap(address(pool), address(router), true);

// 3. Carol is NOT allowlisted. Direct swap reverts:
vm.prank(carol);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(carol, true, 1e18, 0, "", "");

// 4. Carol routes through the public router — swap SUCCEEDS despite not being allowlisted:
vm.prank(carol);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        recipient:       carol,
        zeroForOne:      true,
        amountIn:        1e18,
        amountOutMinimum: 0,
        priceLimitX64:   0,
        deadline:        block.timestamp + 1,
        extensionData:   ""
    })
);
// Carol receives output tokens. The allowlist was bypassed.
// Extension checked allowedSwapper[pool][router] == true, not allowedSwapper[pool][carol].
```

**Root cause:** `SwapAllowlistExtension.beforeSwap` at [1](#0-0)  checks `sender` which equals the router address when routed through `MetricOmmSimpleRouter`, not the originating user. The pool passes `msg.sender` as `sender` at [2](#0-1) , and the router is the pool's `msg.sender` at [3](#0-2) . The extension dispatch path is confirmed at [4](#0-3) .

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
