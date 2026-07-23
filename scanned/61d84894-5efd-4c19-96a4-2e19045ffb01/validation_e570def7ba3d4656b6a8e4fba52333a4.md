### Title
`DepositAllowlistExtension` Checks LP Recipient (`owner`) Instead of Actual Depositor (`sender`), Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and instead checks the user-supplied `owner` address (the LP position recipient) against the allowlist. Any address not on the allowlist can bypass the restriction by calling `addLiquidity` with `owner` set to any allowlisted address.

---

### Finding Description

In `MetricOmmPool.addLiquidity`, the pool calls the extension hook with `sender = msg.sender` (the actual depositor) and `owner` = the user-supplied LP recipient: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both: [2](#0-1) 

However, `DepositAllowlistExtension.beforeAddLiquidity` names the first parameter with a blank (`address,`), discarding `sender` entirely, and performs the allowlist check only on `owner`: [3](#0-2) 

Because `msg.sender` inside the extension is the pool (enforced by `onlyPool`), the check `allowedDepositor[msg.sender][owner]` resolves to `allowedDepositor[pool][owner]` — it asks whether the **LP recipient** is allowlisted, not whether the **depositor** is allowlisted.

The inconsistency is confirmed by comparing with `SwapAllowlistExtension.beforeSwap`, which correctly names and checks `sender`: [4](#0-3) 

---

### Impact Explanation

Any address not on the allowlist can call `pool.addLiquidity(owner = <any_allowlisted_address>, ...)`. The extension sees the allowlisted `owner`, passes the check, and the deposit proceeds. The attacker modifies pool bin state (token balances, share totals) without authorization. LP shares are credited to the allowlisted address, but the attacker has already altered the pool's liquidity distribution — affecting bin prices, per-share value metrics used by `OracleValueStopLossExtension`, and the returns of existing LPs. The deposit allowlist — the pool admin's primary access-control mechanism for liquidity — is rendered completely ineffective.

---

### Likelihood Explanation

Exploitation requires only knowing one allowlisted address, which is publicly readable on-chain via `allowedDepositor(pool, address)`. No special privileges, flash loans, or complex setup are needed. Any EOA or contract can execute the bypass in a single transaction.

---

### Recommendation

Name and check `sender` (the actual depositor) instead of `owner` (the LP recipient), mirroring the correct pattern in `SwapAllowlistExtension`:

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

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` as an extension.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is permitted.
3. Attacker (`bob`, not allowlisted) calls:
   ```solidity
   pool.addLiquidity(
       owner = alice,   // allowlisted address
       salt  = 0,
       deltas = <any valid delta>,
       callbackData = ...,
       extensionData = ""
   );
   ```
4. Pool calls `extension.beforeAddLiquidity(sender=bob, owner=alice, ...)`.
5. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. `bob`'s tokens are pulled from `bob` (via the swap callback), LP shares are credited to `alice`.
7. `bob` has successfully deposited into a restricted pool, modifying bin balances and share totals without being on the allowlist.
8. `bob` can repeat with different bin configurations to manipulate the pool's liquidity distribution at will. [3](#0-2)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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
