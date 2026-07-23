### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Non-Allowlisted Addresses to Bypass the Deposit Guard — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently ignores the `sender` argument (the address that actually calls `addLiquidity` and provides tokens via callback) and instead gates on `owner` (the LP-position beneficiary). Any address that is not on the allowlist can bypass the guard by naming an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

```
_beforeAddLiquidity(msg.sender /*sender*/, owner /*owner*/, salt, deltas, extensionData);
``` [1](#0-0) 

`sender` is the EOA or contract that called `addLiquidity` and will be asked to pay tokens through the swap-callback. `owner` is the address that will hold the resulting LP shares and is the only address that can later call `removeLiquidity`.

`DepositAllowlistExtension.beforeAddLiquidity` discards `sender` entirely (unnamed first parameter) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [2](#0-1) 

The sibling `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the first parameter):

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

The inconsistency is the root cause: the deposit guard was written to mirror the swap guard's parameter layout but the wrong positional argument was selected.

---

### Impact Explanation

The `DepositAllowlistExtension` is the only on-chain mechanism a pool admin has to restrict who may inject liquidity. When it checks `owner` instead of `sender`:

1. **Allowlist bypass**: Any non-allowlisted address `B` can call `pool.addLiquidity(owner = A, ...)` where `A` is any allowlisted address. The guard passes because `A` is allowlisted; `B` is never checked.
2. **Token injection without authorization**: `B` provides the tokens through the callback. The pool's bin balances and `binTotals` are updated with `B`'s tokens, changing the pool's liquidity distribution without the pool admin's consent.
3. **LP-position recovery**: Because `removeLiquidity` enforces `msg.sender == owner`, `A` (or a contract `B` controls at address `A`) can immediately call `removeLiquidity` to retrieve the tokens. If `B` controls `A`, the round-trip is atomic: deposit bypassing the allowlist → withdraw → net effect is zero cost to `B` but the guard was circumvented.
4. **Manipulation surface**: A non-allowlisted actor can repeatedly shift bin balances (e.g., concentrate liquidity in a specific bin) to influence the oracle-anchored swap prices seen by other users, causing bad-price execution for legitimate swappers.

The pool admin's core invariant — "only allowlisted addresses may add liquidity" — is broken for every pool that deploys this extension.

---

### Likelihood Explanation

- No special privilege is required; any externally-owned account or contract can call `addLiquidity`.
- The only prerequisite is knowing one allowlisted address, which is public on-chain (emitted in `AllowedToDepositSet` events or readable from `allowedDepositor`).
- The exploit is a single transaction with no upfront cost beyond gas and the tokens deposited (which are recoverable if the attacker controls the `owner` address).
- Pools that use `DepositAllowlistExtension` for regulatory (KYC/AML) or risk-management purposes are immediately affected upon deployment.

---

### Recommendation

Replace the unnamed first parameter with `sender` and gate on it, matching the pattern used by `SwapAllowlistExtension`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol

function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intended semantic is "the LP-position owner must be allowlisted" (e.g., to prevent allowlisted LPs from gifting positions to non-allowlisted parties), both `sender` and `owner` should be checked.

---

### Proof of Concept

**Setup**
- Pool `P` is deployed with `DepositAllowlistExtension` at address `E`.
- Pool admin calls `E.setAllowedToDeposit(P, Alice, true)`. Bob is **not** allowlisted.

**Attack**
```
// Bob (not allowlisted) calls addLiquidity naming Alice as owner
pool.addLiquidity(
    owner        = Alice,   // allowlisted → guard passes
    salt         = 0,
    deltas       = <desired bin shares>,
    callbackData = "",
    extensionData= ""
);
// Pool calls back to Bob (msg.sender) for tokens — Bob pays
// Alice's LP position is minted with Bob's tokens
```

**Guard check (line 38)**
```
allowedDepositor[P][Alice] == true  →  no revert
```

`sender = Bob` is never evaluated.

**Recovery (if Bob controls Alice)**
```
pool.removeLiquidity(owner=Alice, salt=0, deltas=<same>, extensionData="");
// Alice (Bob's contract) receives tokens back
```

Net result: Bob deposited into a restricted pool, manipulated bin balances, and recovered his tokens — the allowlist provided zero protection. [2](#0-1) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L188-195)
```text
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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
