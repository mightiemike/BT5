Based on my research, I have enough to synthesize a finding. Let me trace the key path:

**Core observation:** `SwapAllowlistExtension.beforeSwap` checks `sender` (the first parameter), which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router**, not the actual user. The extension therefore checks whether the **router** is allowlisted, not the actual swapper.

This is the direct analog to the ERC1820 bug: the guard checks the wrong identity, making it too permissive for all users who route through the router.

---

### Title
`SwapAllowlistExtension` Gates Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which the pool sets to `msg.sender` of the `swap` call. When a user routes through the public `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the actual user. If the pool admin allowlists the router (to permit router-mediated swaps for their allowlisted users), every unprivileged address can bypass the per-user allowlist by routing through the router.

### Finding Description

`SwapAllowlistExtension` is described as gating `swap` by swapper address, per pool: [1](#0-0) 

Its `beforeSwap` hook checks the first parameter `sender` against the per-pool allowlist: [2](#0-1) 

The pool's `ExtensionCalling._beforeSwap` populates `sender` directly from `msg.sender` of the `swap` call: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter` (a public, permissionless contract), the router calls `pool.swap(...)` as `msg.sender`. The pool therefore passes the **router's address** as `sender` to the extension. The extension evaluates `allowedSwapper[pool][router_address]` — not `allowedSwapper[pool][actual_user]`.

The `IMetricOmmExtensions.beforeSwap` interface confirms `sender` is the first positional argument: [4](#0-3) 

The pool's `swap` function passes `msg.sender` as `sender` and `recipient` as the output address: [5](#0-4) 

### Impact Explanation

A pool admin deploys a restricted pool with `SwapAllowlistExtension` to gate swaps to a specific set of addresses (e.g., KYC-verified counterparties or institutional traders). To allow those allowlisted users to use the public router, the admin adds the router to the allowlist. At that point, **any address** — including non-allowlisted ones — can call `MetricOmmSimpleRouter` and have their swap pass the extension check, because the extension sees `sender = router` (allowlisted) rather than the actual caller. The allowlist is completely bypassed for all router-mediated swaps, allowing unauthorized parties to trade on a pool that was intended to be restricted. This constitutes broken core pool functionality with direct fund impact: unauthorized swaps drain liquidity and generate adverse price impact against the pool's LPs.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is a public, permissionless periphery contract. Any user can call it. The bypass requires only that the pool admin has allowlisted the router — a natural and expected administrative action when the admin wants their allowlisted users to be able to use the router. The attack requires no privileged access, no special tokens, and no malicious setup; it is reachable by any address in a single transaction. [6](#0-5) 

### Recommendation

The extension should gate the **economic actor** (the address that benefits from the swap output), not the direct `msg.sender` of the pool call. Two options:

1. **Check `recipient` instead of `sender`**: The `recipient` parameter is the address that receives the output tokens and is the economically relevant identity. Gate on `allowedSwapper[msg.sender][recipient]`.
2. **Propagate the original user through the router**: Have the router pass the original `msg.sender` as part of `extensionData`, and have the extension decode and check that identity. This requires a coordinated change to the router and extension.

Option 1 is simpler and consistent with how `DepositAllowlistExtension` correctly gates `owner` (the LP position beneficiary) rather than `sender`: [7](#0-6) 

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, router, true)` to allowlist the `MetricOmmSimpleRouter`.
3. Non-allowlisted address `attacker` calls `MetricOmmSimpleRouter.exactInput(...)` targeting the restricted pool.
4. The router calls `pool.swap(recipient=attacker, ...)` with `msg.sender = router`.
5. The pool calls `extension.beforeSwap(sender=router, recipient=attacker, ...)`.
6. The extension evaluates `allowedSwapper[pool][router] == true` → passes.
7. The swap executes. `attacker` receives output tokens despite never being allowlisted.
8. Direct pool call from `attacker` (without the router) would revert: `allowedSwapper[pool][attacker] == false`. [2](#0-1)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
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
