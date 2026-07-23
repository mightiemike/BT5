### Title
SwapAllowlistExtension Gates Router Address Instead of Actual Swapper, Allowing Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` to the pool (i.e., the `MetricOmmSimpleRouter` contract address when users route through it). This means the allowlist gates the router's identity, not the actual end-user's identity. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the pool to every user, bypassing the intended per-user access control.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its access check as follows: [1](#0-0) 

The check is `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool (correct) and `sender` is the address the pool passes through `ExtensionCalling._beforeSwap`: [2](#0-1) 

The pool populates `sender` from its own `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the direct caller is the router contract, so `sender = router`. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted.

The `DepositAllowlistExtension` has the symmetric design but checks `owner` (the LP position holder), which is the economically correct identity for deposit gating: [3](#0-2) 

The swap extension has no equivalent forwarding of the original user identity.

### Impact Explanation

Two fund-impacting scenarios arise:

**Scenario A — Allowlist bypass (Critical/High):** A pool admin configures `SwapAllowlistExtension` to restrict swaps to a set of trusted counterparties (e.g., whitelisted market makers). To allow those counterparties to use the router, the admin also allowlists the router address. Because `sender = router` for every router-mediated call, any unprivileged user can now swap in the restricted pool by routing through `MetricOmmSimpleRouter`. The allowlist is completely bypassed. LP funds are exposed to unrestricted toxic flow that the pool was explicitly designed to exclude.

**Scenario B — Broken core functionality (Medium):** A pool admin allowlists individual user addresses. Those users attempt to swap through the router. The extension sees `sender = router` (not allowlisted) and reverts with `NotAllowedToSwap`. Allowlisted users cannot use the primary public swap path, making the pool's swap functionality unusable for the intended participants.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public entry point for swaps. Any pool that deploys `SwapAllowlistExtension` and expects users to interact via the router will encounter this mismatch. Scenario A requires the admin to allowlist the router (a natural step when enabling router access), which is a realistic and likely configuration. Scenario B is triggered by any allowlisted user who uses the router rather than calling the pool directly. [4](#0-3) 

### Recommendation

The `beforeSwap` hook should check the **original user** identity, not the intermediary router. Two approaches:

1. **Pass original sender through the router:** `MetricOmmSimpleRouter` should forward the original `msg.sender` as the `sender` argument to `pool.swap()`, and the pool should pass that value (not its own `msg.sender`) to the extension. This requires a trusted-forwarder pattern with the pool verifying the router's identity before accepting a delegated sender.

2. **Check both sender and recipient in the extension:** Gate on `recipient` (the address that receives output tokens) in addition to or instead of `sender`, since `recipient` is typically the actual user even in router-mediated flows.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intending to allow router-mediated swaps for allowlisted users.
3. Attacker (not individually allowlisted) calls:
       MetricOmmSimpleRouter.exactInput(pool, tokenIn, tokenOut, amountIn, ...)
4. Router calls pool.swap(sender=router, recipient=attacker, ...)
5. Pool calls _beforeSwap(sender=router, ...)
6. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes.
7. Attacker's swap executes in a pool that was supposed to be restricted.
8. LP funds are exposed to attacker's flow; pool invariant violated.
``` [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
