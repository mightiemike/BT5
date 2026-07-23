### Title
`DepositAllowlistExtension` Guards `owner` but Token Settlement Flows Through `sender` — Deposit Restriction Fully Bypassed - (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` validates the `owner` (position recipient) address against the allowlist, but `addLiquidity` explicitly permits `msg.sender != owner` (the operator pattern). The actual token transfer is pulled from `msg.sender` (`sender`) via the modify-liquidity callback — not from `owner`. Any non-allowlisted address can therefore deposit tokens into a restricted pool by supplying an allowlisted address as `owner`, bypassing the guard entirely.

### Finding Description

`addLiquidity` separates the token provider (`msg.sender`, called `sender` in extension hooks) from the position recipient (`owner`):

> "msg.sender pays but need not equal owner (operator pattern)." [1](#0-0) 

The pool passes both addresses to `_beforeAddLiquidity`, which forwards them to every configured extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the first parameter (`sender`) and only checks `owner`: [3](#0-2) 

Because the allowlist check passes as long as `owner` is allowlisted, any non-allowlisted `msg.sender` can call `pool.addLiquidity(allowlistedAddress, salt, ...)`, satisfy the callback with their own tokens, and have those tokens accepted by the pool. The guard that was supposed to restrict who can deposit is never applied to the address that actually provides the funds.

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting LP participation (e.g., KYC/AML compliance, institutional-only pools). Because the guard checks the wrong actor, the restriction is hollow: any address can deposit tokens into a nominally restricted pool. The non-allowlisted depositor loses their tokens to the allowlisted address's LP position, but the pool receives tokens from an unauthorized source. A colluding allowlisted address can then call `removeLiquidity` (which requires `msg.sender == owner`) and return the proceeds off-chain, completing a full deposit-and-withdraw cycle by an address the pool admin explicitly excluded. This is a direct admin-boundary break: a pool admin-configured access control is bypassed by an unprivileged path with no special permissions required.

### Likelihood Explanation

The operator pattern (`msg.sender != owner`) is a documented, first-class feature of `addLiquidity`. Any non-allowlisted address that knows one allowlisted address (which may be publicly visible on-chain from prior `LiquidityAdded` events) can execute the bypass in a single transaction. No flash loans, price manipulation, or privileged access are required.

### Recommendation

Change `DepositAllowlistExtension.beforeAddLiquidity` to check `sender` (the actual token provider, i.e., `msg.sender` of `addLiquidity`) instead of — or in addition to — `owner`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    // Check the address that actually provides tokens, not just the position recipient.
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to restrict both who can be a position owner AND who can provide tokens, both `sender` and `owner` should be checked independently.

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` configured. Only address `ALLOWED` is added to the allowlist via `setAllowedToDeposit(pool, ALLOWED, true)`.
2. Non-allowlisted address `ATTACKER` constructs a call: `pool.addLiquidity(ALLOWED, salt, deltas, callbackData, extensionData)`.
3. The pool calls `_beforeAddLiquidity(ATTACKER, ALLOWED, ...)`, which calls `DepositAllowlistExtension.beforeAddLiquidity(ATTACKER, ALLOWED, ...)`.
4. The extension evaluates `allowedDepositor[pool][ALLOWED]` → `true`. No revert.
5. The pool issues a modify-liquidity callback to `ATTACKER` (msg.sender), which transfers tokens from `ATTACKER` into the pool.
6. The LP position is credited to `ALLOWED`.
7. `ALLOWED` calls `removeLiquidity(ALLOWED, salt, ...)` and receives the tokens back, sharing proceeds with `ATTACKER` off-chain.

`ATTACKER` has deposited into a pool from which it was explicitly excluded. The allowlist check on `owner` is satisfied throughout; the check on the actual token provider (`sender`) is never performed. [3](#0-2) [4](#0-3) [2](#0-1)

### Citations

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-162)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
  /// @param salt Namespace byte width for the key (`uint80`).
  /// @param deltas Parallel `binIdxs` / `shares` arrays (see `LiquidityDelta`).
  /// @param callbackData Opaque bytes forwarded unmodified to the modify-liquidity callback.
  /// @param extensionData Opaque bytes forwarded to liquidity extensions (beforeAddLiquidity / afterAddLiquidity).
  /// @return amount0Added Total token0 actually pulled from the callback into the pool (native).
  /// @return amount1Added Total token1 actually pulled from the callback into the pool (native).
  /// @dev Reverts `LiquidityDeltaLengthMismatch` when `binIdxs` and `shares` lengths differ.
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external returns (uint256 amount0Added, uint256 amount1Added);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
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
