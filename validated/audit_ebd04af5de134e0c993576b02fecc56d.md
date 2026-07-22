### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any non-allowlisted address to bypass the deposit guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` gates liquidity provision by checking the `owner` parameter (the position beneficiary) rather than the `sender` parameter (the actual caller). Because `owner` is a free caller-supplied argument to `MetricOmmPool.addLiquidity`, any non-allowlisted address can bypass the guard entirely by nominating any allowlisted address as `owner`.

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` address and forwards both `msg.sender` (as `sender`) and `owner` to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` passes both values faithfully to every configured extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but silently discards it (unnamed `address`), and instead checks only `owner`: [3](#0-2) 

Compare this with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual caller): [4](#0-3) 

The asymmetry is the root cause. Because `owner` is freely chosen by the caller, the allowlist check is trivially satisfied by any non-allowlisted address that names any allowlisted address as `owner`.

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may provide liquidity (e.g., for regulatory compliance, curated LP sets, or preventing adversarial liquidity). With this bug the guard is completely ineffective:

- A non-allowlisted address calls `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)`.
- The extension checks `allowedDepositor[pool][allowlistedAddress]` → `true` → passes.
- The non-allowlisted caller's `metricOmmSwapCallback` is invoked; the caller pays the tokens.
- A position is minted under `owner = allowlistedAddress`.

The non-allowlisted party has injected liquidity into a restricted pool, shifting bin balances, `curBinIdx`, and `curPosInBin` in ways the admin did not authorize. This can distort oracle-anchored swap prices for all subsequent traders and dilute or manipulate LP returns — a direct fund-impacting consequence for existing LPs and swappers. The allowlisted address receives an unsolicited position it did not initiate.

### Likelihood Explanation

Exploitation requires no special privilege: any externally-owned account or contract that is not on the allowlist can trigger this by a single `addLiquidity` call with a known allowlisted address as `owner`. The allowlisted addresses are discoverable on-chain via `allowedDepositor` mapping events. Likelihood is high.

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositor) instead of `owner` (the position beneficiary), mirroring the correct pattern in `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  alice  → allowedDepositor[pool][alice] = true
  bob    → allowedDepositor[pool][bob]   = false  (non-allowlisted)

Attack:
  vm.prank(bob);
  pool.addLiquidity(
      alice,          // owner — allowlisted, check passes
      salt,
      deltas,
      callbackData,   // bob's callback pays tokens
      extensionData
  );

Result:
  - Extension check: allowedDepositor[pool][alice] == true → no revert
  - Bob's tokens transferred to pool via callback
  - Position minted for alice (owner)
  - Bob has deposited into a pool he is not authorized to touch
  - Pool bin state altered without admin consent
``` [3](#0-2) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
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
