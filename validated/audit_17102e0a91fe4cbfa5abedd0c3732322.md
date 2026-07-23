Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the router becomes `sender`, not the actual user. Allowlisting the router — the only way to enable router-mediated swaps for legitimate users — simultaneously grants every unprivileged user the ability to bypass the per-pool allowlist in a single transaction.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool via `CallExtension.callExtension`) and `sender` is the first argument forwarded from the pool. In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    ...
```

`ExtensionCalling._beforeSwap` forwards this unchanged to the extension. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The router's address becomes `msg.sender` of `pool.swap`, so `sender` in the extension is the router, not the originating user. The extension never observes the end user's address.

**Consequence — two mutually exclusive failure modes:**

| Configuration | Allowlisted user (direct) | Allowlisted user (router) | Non-allowlisted user (router) |
|---|---|---|---|
| Router NOT allowlisted | ✅ passes | ❌ reverts | ❌ reverts |
| Router IS allowlisted | ✅ passes | ✅ passes | ✅ passes ← **bypass** |

There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users from doing the same. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all four router entry points call `pool.swap` with `msg.sender = router`.

Existing guards are insufficient: the extension has no mechanism to inspect the original caller, and `extensionData` is passed through from the router as-is (the router passes `params.extensionData` directly without appending caller identity).

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC'd participants, designated market makers) is fully bypassed by any EOA or contract calling `MetricOmmSimpleRouter`. The pool admin's intended access control is silently nullified. LPs in such a pool suffer toxic flow from actors the pool was designed to exclude, resulting in direct LP principal loss through adverse selection. This constitutes a broken core pool functionality / admin-boundary break causing direct loss of LP assets.

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public, permissionless periphery contract. No special privilege, flash loan, or multi-step setup is required. The bypass is reachable in a single transaction by any EOA or contract. The precondition — router being allowlisted — is a necessary operational step for any pool admin who wants allowlisted users to use the router, making the vulnerable configuration the expected production configuration.

## Recommendation
The extension must gate the economically relevant actor — the end user — not the intermediary router. The cleanest fix: the router appends `abi.encode(msg.sender)` to `extensionData` for each hop, and the extension decodes and checks that address instead of `sender`. This requires the extension to trust only known router addresses (verified via a factory registry or immutable allowlist) to prevent spoofing via crafted `extensionData`.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin: setAllowedToSwap(pool, userA, true)
  - Pool admin: setAllowedToSwap(pool, router, true)
    (required so userA can use the router at all)

Attack:
  - userB (not allowlisted) calls:
      router.exactInputSingle({
          pool: pool,
          recipient: userB,
          zeroForOne: true,
          amountIn: X,
          ...
      })

  Call chain:
    userB → router.exactInputSingle()
           → pool.swap(msg.sender = router)          [MetricOmmPool.sol L231]
             → _beforeSwap(sender = router, ...)     [ExtensionCalling.sol L160]
               → extension.beforeSwap(sender = router)
                 → allowedSwapper[pool][router] == true → PASSES  [SwapAllowlistExtension.sol L37]

  Result: userB executes a swap the allowlist was designed to block.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
