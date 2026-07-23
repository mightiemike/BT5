Looking at the extension framework, I need to examine how the `DepositAllowlistExtension` gates deposits and whether the parameter it checks matches the actual actor performing the deposit. [1](#0-0) 

The `beforeAddLiquidity` hook receives `(sender, owner, salt, deltas, extensionData)` — `sender` is `msg.sender` of the pool call (the actual depositor providing tokens via callback), and `owner` is the address that will hold the LP position. [2](#0-1) 

The pool passes both correctly. But the extension ignores `sender` (first arg, unnamed) and checks only `owner`: [3](#0-2) 

Compare with `SwapAllowlistExtension`, which correctly checks `sender`: [4](#0-3) 

The pool's `addLiquidity` imposes no restriction on who can be `owner` — any caller can pass any address: [5](#0-4) 

`removeLiquidity` enforces `msg.sender == owner`, but `addLiquidity` has no such check, making the `owner` field fully attacker-controlled. [6](#0-5) 

---

### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
`DepositAllowlistExtension.beforeAddLiquidity` validates the LP-position recipient (`owner`) rather than the actual caller (`sender`). Because `addLiquidity` accepts any `owner` address with no restriction, any non-allowlisted address can pass the allowlist check by supplying an allowlisted address as `owner`, while itself acting as the depositor.

### Finding Description
`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. The depositor is `sender` — the address that calls the pool and fulfills the token transfer via the add-liquidity callback. The extension's `beforeAddLiquidity` hook receives `sender` as its first argument but silently discards it (unnamed parameter) and instead checks `allowedDepositor[msg.sender][owner]`:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Because `addLiquidity` places no constraint on the `owner` argument, an attacker can call `pool.addLiquidity(owner = allowlistedAddress, ...)`, causing the extension to evaluate `allowedDepositor[pool][allowlistedAddress]` — which is `true` — and allow the deposit. The attacker provides the tokens via callback; the LP shares are minted to the allowlisted address.

The `SwapAllowlistExtension` does not share this flaw — it correctly checks `sender`.

### Impact Explanation
The deposit allowlist is completely ineffective. Any address can add liquidity to a pool that the admin intended to restrict. Concrete consequences:

1. **Pool state manipulation**: An attacker deposits into specific bins to shift the pool's internal price position, then swaps against those bins. The LP loss is borne by the allowlisted address that involuntarily holds the position.
2. **Griefing of allowlisted LPs**: Allowlisted addresses receive unsolicited LP positions in bins they did not choose, exposing them to impermanent loss and fee dilution.
3. **Broken access-control invariant**: The pool admin's security boundary (restricted liquidity provision) is silently voided with no on-chain signal.

### Likelihood Explanation
Exploitation requires only a standard `addLiquidity` call with `owner` set to any address that appears in `allowedDepositor`. Allowlisted addresses are discoverable from `AllowedToDepositSet` events. No special privilege, flash loan, or oracle manipulation is needed. Any unprivileged address can trigger this at any time.

### Recommendation
Replace the `owner` check with a `sender` check, matching the contract's stated intent and the pattern used by `SwapAllowlistExtension`:

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
1. Pool P is deployed with `DepositAllowlistExtension` configured; `allowedDepositor[P][alice] = true`; `allowedDepositor[P][attacker] = false`.
2. Attacker calls `P.addLiquidity(owner = alice, salt, deltas, callbackData, extensionData)`.
3. Pool calls `_beforeAddLiquidity(msg.sender=attacker, owner=alice, ...)`.
4. Extension receives `(sender=attacker, owner=alice)`, ignores `attacker`, checks `allowedDepositor[P][alice]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` executes; attacker's callback transfers tokens; LP shares are minted to `alice`.
6. Attacker has deposited into a restricted pool, bypassing the allowlist entirely. [1](#0-0) [7](#0-6) [5](#0-4)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
  }
```
