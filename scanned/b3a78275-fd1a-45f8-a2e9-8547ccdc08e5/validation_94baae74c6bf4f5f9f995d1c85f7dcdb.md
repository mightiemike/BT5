### Title
`DepositAllowlistExtension` Checks LP Recipient (`owner`) Instead of Actual Depositor (`sender`), Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` enforces the deposit allowlist against the caller-controlled `owner` parameter (the LP-position recipient) rather than the `sender` parameter (the actual token payer, i.e., `msg.sender` of `addLiquidity`). Any unprivileged address can bypass the allowlist by supplying any allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` address that receives the LP position, while the actual token payment is collected from `msg.sender` via the swap callback: [1](#0-0) 

The pool then forwards both `msg.sender` (as `sender`) and the caller-supplied `owner` to the extension hook: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first (unnamed) argument and `owner` as its second, but silently discards `sender` and gates only on `owner`: [3](#0-2) 

Because `owner` is freely chosen by the caller, any non-allowlisted address can pass the guard by setting `owner` to any address that appears in `allowedDepositor[pool]`.

---

### Impact Explanation

The `DepositAllowlistExtension` is the pool admin's mechanism to restrict which addresses may deposit into a pool (e.g., KYC-gated or private pools). With this bug the restriction is entirely ineffective:

- A non-allowlisted attacker calls `pool.addLiquidity(allowlisted_address, salt, deltas, ...)`.
- The extension checks `allowedDepositor[pool][allowlisted_address]` → passes.
- The attacker pays tokens via the callback; the allowlisted address receives LP shares it never requested.
- The pool admin's access-control invariant is broken: any actor can deposit into a nominally restricted pool.

Secondary effects include forced, unconsented LP exposure on the allowlisted address (griefing) and the ability to manipulate pool liquidity distribution from an unprivileged position.

This is an **admin-boundary break**: a pool-admin security configuration is bypassed by an unprivileged path, matching the allowed impact gate.

---

### Likelihood Explanation

Exploitation requires no special privilege, no flash loan, and no oracle manipulation. Any EOA or contract that knows one allowlisted address (publicly readable from `allowedDepositor`) can execute the bypass in a single transaction. Likelihood is **High**.

---

### Recommendation

Replace the `owner` check with a check on `sender` (the first parameter, which is `msg.sender` of `addLiquidity` — the actual token payer):

```solidity
// DepositAllowlistExtension.sol
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

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`, which correctly gates on `sender` (the swap initiator): [4](#0-3) 

---

### Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  bob is NOT allowlisted

Attack:
  bob calls pool.addLiquidity(
      owner    = alice,   // allowlisted — passes the guard
      salt     = 0,
      deltas   = <valid delta>,
      callbackData = <bob pays tokens in callback>,
      extensionData = ""
  )

Result:
  - Extension checks allowedDepositor[pool][alice] → true → no revert
  - Bob's tokens are transferred into the pool via metricOmmSwapCallback
  - Alice receives LP shares she did not request
  - Bob has deposited into a pool he is not authorized to access
  - The allowlist is completely bypassed
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
