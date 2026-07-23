### Title
`SwapAllowlistExtension` Gates on Router Address Instead of Actual User — Allowlist Fully Bypassed When Router Is Allowlisted - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of the pool call, so the extension checks the router's allowlist status — not the actual user's. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the per-user swap allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then gates on that `sender`:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(params.recipient, ...)` directly — the user's address is stored only in transient storage for the payment callback and is never forwarded to the pool:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

From the pool's perspective `msg.sender = router`, so `sender = router` in the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

A pool admin who wants to support both direct and router-mediated swaps for a restricted set of users will allowlist the router. The moment the router is allowlisted, the per-user gate is gone: any address can call `MetricOmmSimpleRouter.exactInputSingle` and the extension sees only the allowlisted router address, granting the swap unconditionally.

This is structurally identical to the external report's bug class: a state-modifying path (router-mediated swap) skips the guard that every other path (direct swap) correctly enforces, because the guard checks the wrong identity on that path.

Note the asymmetry with `DepositAllowlistExtension`, which correctly gates on `owner` (the economic actor) rather than `sender` (the direct caller), making it robust to the liquidity adder intermediary:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

`SwapAllowlistExtension` has no equivalent `owner`/initiator field to gate on because the swap interface does not carry the originating user's identity through to the pool.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., a private pool for whitelisted market makers or KYC'd counterparties) is fully open to any user the moment the router is allowlisted. Non-allowlisted users can execute swaps of arbitrary size, draining LP-owned token reserves at oracle-derived prices. Because the pool's bin accounting and fee accrual are updated by every swap, unauthorized swaps directly reduce LP principal and distort pool state.

---

### Likelihood Explanation

Medium. The trigger requires the pool admin to allowlist the router — a natural and expected action for any pool that intends to support the standard periphery UX. The router is a public, permissionless contract; once it is allowlisted, the bypass is available to every Ethereum address with no further preconditions. The attacker needs no special role, no flash loan, and no privileged access.

---

### Recommendation

Gate `SwapAllowlistExtension.beforeSwap` on the economic initiator rather than the direct caller. Two options:

1. **Check `recipient` as a proxy for the initiating user** — only viable if the pool's usage convention guarantees `recipient == initiator`, which is not enforced.
2. **Require callers to attest their identity via `extensionData`** — the router or user passes the real initiator address in `extensionData`; the extension verifies it. This requires a coordinated change to the router.
3. **Document that the router must never be allowlisted** and enforce this at the admin-setter level by rejecting known router addresses — a fragile mitigation.

The cleanest fix is option 2: extend the `beforeSwap` hook to accept an attested initiator in `extensionData` and have the router always populate it with `msg.sender` before forwarding to the pool.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured; allowlist only `alice` and `MetricOmmSimpleRouter`.
2. Call `SwapAllowlistExtension.isAllowedToSwap(pool, bob)` → returns `false`.
3. `bob` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`.
4. Router calls `pool.swap(bob, ...)` with `msg.sender = router`.
5. Pool calls `extension.beforeSwap(sender=router, ...)`.
6. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `bob` receives output tokens; the pool's LP reserves are reduced. The allowlist did not block `bob`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
