### Title
SwapAllowlistExtension Gates on Router Address Instead of End User, Allowing Non-Allowlisted Users to Bypass Swap Restrictions — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. If the router is allowlisted (which is required for any allowlisted user to use the router), every user — including non-allowlisted ones — can bypass the per-user swap restriction by routing through the router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to the extension: [1](#0-0) 

`_beforeSwap` encodes it as the first argument of `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user routes through `MetricOmmSimpleRouter`, the router calls `pool.swap()`, so `sender` = router address. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

The pool admin faces an impossible choice:
- **Router not allowlisted**: allowlisted users cannot use the router at all.
- **Router allowlisted**: every user — including non-allowlisted ones — can bypass the per-user restriction by routing through the router.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` parameter (the economic beneficiary), not the caller, so it does not share this flaw: [4](#0-3) 

The router interface confirms that the end user's identity is never forwarded to the pool — only `recipient` (output token destination) and opaque `extensionData` are passed, neither of which the `SwapAllowlistExtension` reads: [5](#0-4) 

### Impact Explanation

Any user can bypass a pool's swap allowlist by calling `MetricOmmSimpleRouter.exactInputSingle` (or any router entry point) targeting a pool whose admin has allowlisted the router. The allowlist — intended to restrict trading to specific counterparties — provides no protection against router-mediated swaps. This breaks the core access-control invariant of the extension and constitutes an admin-boundary break where an unprivileged path circumvents a configured pool guard.

### Likelihood Explanation

The router is a standard periphery contract that users are expected to use for slippage protection (`amountOutMinimum`, `deadline`). Any pool admin who wants allowlisted users to benefit from router features must allowlist the router, at which point the bypass is immediately available to all users. No special privileges, flash loans, or unusual token behavior are required — a standard `exactInputSingle` call suffices.

### Recommendation

The extension should check the economically relevant actor, not the immediate caller. Two options:

1. **Pass the originating user through `extensionData`**: Have the router encode the end user's address in `extensionData` and have the extension decode and check it. This requires a coordinated convention between router and extension.
2. **Check `recipient` instead of `sender`**: If the pool's design intent is to gate who *receives* output, check `recipient`. If the intent is to gate who *initiates* the swap, the extension must receive the originating user's address through a trusted channel (e.g., a router that signs or encodes the caller).

The cleanest fix is to redesign the extension to accept an authenticated caller identity via `extensionData`, similar to how `DepositAllowlistExtension` uses the `owner` parameter (which the pool itself enforces as the economic beneficiary).

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Admin calls `setAllowedToSwap(pool, router, true)` — required so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Bob successfully swaps despite not being on the allowlist.

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/contracts/interfaces/IMetricOmmSimpleRouter.sol (L81-92)
```text
  struct ExactInputSingleParams {
    address pool;
    address tokenIn;
    address tokenOut;
    bool zeroForOne;
    uint128 amountIn;
    uint128 amountOutMinimum;
    address recipient;
    uint256 deadline;
    uint128 priceLimitX64;
    bytes extensionData;
  }
```
