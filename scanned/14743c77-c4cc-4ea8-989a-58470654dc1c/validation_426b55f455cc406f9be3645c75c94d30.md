### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension` is designed to gate `addLiquidity` by depositor address. Its `beforeAddLiquidity` hook silently ignores the `sender` argument (the actual caller who pays tokens) and instead validates `owner` (the LP-position recipient). Because `addLiquidity` lets the caller freely choose `owner`, any address not on the allowlist can deposit tokens into the pool by nominating an allowlisted address as `owner`, bypassing the guard entirely.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook: [1](#0-0) 

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`msg.sender` is the actual caller who will be called back to supply tokens; `owner` is the address that receives the LP shares. `ExtensionCalling` forwards both faithfully: [2](#0-1) 

Inside the extension, however, the `sender` slot is discarded (`address,`) and only `owner` is checked: [3](#0-2) 

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Compare with `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender` (the actual caller of `swap`): [4](#0-3) 

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

The asymmetry is the root cause: `SwapAllowlistExtension` guards the right actor; `DepositAllowlistExtension` guards the wrong one.

---

### Impact Explanation

An unauthorized caller (Bob) calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)` where Alice is on the allowlist. The hook sees `owner = alice` → allowlist passes. The pool then calls back `msg.sender` (Bob) to collect the tokens. Bob pays; Alice receives the LP position. The pool has accepted tokens from an actor the admin explicitly excluded. The pool admin's access-control invariant — that only allowlisted addresses can deposit — is broken. If the allowlist is used for regulatory compliance or to restrict liquidity to trusted counterparties, the bypass has direct protocol-level consequences. Additionally, Bob can grief Alice by forcing an unwanted LP position onto her address, since `removeLiquidity` requires `msg.sender == owner`: [5](#0-4) 

meaning Alice must actively unwind a position she never requested.

---

### Likelihood Explanation

The trigger requires no special privilege: any EOA or contract can call `addLiquidity` with an arbitrary `owner`. The only prerequisite is knowing at least one allowlisted address (publicly readable from `allowedDepositor` or `allowAllDepositors`). No admin cooperation, no flash loan, no oracle manipulation is needed.

---

### Recommendation

Replace the ignored first parameter with `sender` and validate it instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

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

If the intent is to gate both the payer and the position owner, both `sender` and `owner` should be checked independently.

---

### Proof of Concept

```
Setup
─────
1. Pool P is deployed with DepositAllowlistExtension E.
2. Pool admin calls E.setAllowedToDeposit(P, alice, true).
   → allowedDepositor[P][alice] = true
   → allowedDepositor[P][bob]   = false  (bob is NOT allowed)

Attack
──────
3. Bob (not on allowlist) constructs a router contract R that:
   a. Calls P.addLiquidity(alice, salt, deltas, callbackData, "").
   b. Implements the liquidity callback to transfer the required tokens to P.

4. Inside P.addLiquidity:
   - _beforeAddLiquidity(R, alice, ...) is called.
   - Extension E receives: sender=R (ignored), owner=alice.
   - Check: allowedDepositor[P][alice] == true → passes.
   - LiquidityLib credits LP shares to alice.
   - Callback fires on R; R transfers tokens to P.

Result
──────
5. Bob (via R) has deposited tokens into P without being on the allowlist.
6. Alice holds an LP position she never requested.
7. Pool admin's deposit restriction is silently violated.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-98)
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
