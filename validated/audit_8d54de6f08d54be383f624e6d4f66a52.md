### Title
Permissionless `creditDeposit()` with `balanceOf(address(this))` Enables Forced Balance Inflation to Block Liquidations — (`core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` is callable by any unprivileged address and deposits the **entire** live `balanceOf(address(this))` for every registered spot token into the DDA's fixed `subaccount`. Because the DDA accepts direct ERC20 transfers and the deposit trigger is permissionless, an attacker can force-inflate any subaccount's on-chain balance, preventing a legitimate liquidation and corrupting the protocol's solvency invariant.

---

### Finding Description

`DirectDepositV1.creditDeposit()` carries no access-control modifier:

```solidity
// core/contracts/DirectDepositV1.sol  L83-L101
function creditDeposit() external {
    uint32[] memory productIds = spotEngine.getProductIds();
    for (uint256 i = 0; i < productIds.length; i++) {
        uint32 productId = productIds[i];
        address tokenAddr = spotEngine.getToken(productId);
        require(tokenAddr != address(0), "Invalid productId.");
        IIERC20Base token = IIERC20Base(tokenAddr);
        uint256 balance = token.balanceOf(address(this));   // ← entire live balance
        if (balance != 0) {
            token.approve(address(endpoint), balance);
            endpoint.depositCollateralWithReferral(
                subaccount,          // ← fixed at construction
                productId,
                uint128(balance),
                "-1"
            );
        }
    }
}
```

The same permissionless path is exposed through `ContractOwner.creditDepositV1(bytes32 subaccount)`:

```solidity
// core/contracts/ContractOwner.sol  L502-L508
function creditDepositV1(bytes32 subaccount) external {
    address payable directDepositV1 = directDepositV1Address[subaccount];
    if (directDepositV1 == address(0)) {
        directDepositV1 = createDirectDepositV1(subaccount);
    }
    DirectDepositV1(directDepositV1).creditDeposit();
}
```

Every DDA address is publicly readable from `ContractOwner.directDepositV1Address`. An attacker can:

1. Look up the DDA address for any target `subaccount`.
2. Transfer any registered ERC20 token directly to that DDA (standard `transfer`, no `selfdestruct` required).
3. Call `creditDeposit()` (or `creditDepositV1`) — the function reads `token.balanceOf(address(this))`, which now includes the attacker's donated tokens, and deposits the full inflated amount into the target subaccount.

The `receive()` function additionally wraps any ETH sent normally into WETH, which is itself a registered product, so the same path applies to native-token inflation.

---

### Impact Explanation

Nado's liquidation engine (`ClearinghouseLiq`, delegatecalled from `Clearinghouse.liquidateSubaccount`) only liquidates a subaccount when `getHealth(subaccount, MAINTENANCE) < 0`. Health is computed from the subaccount's on-chain balance entries in `SpotEngine` and `PerpEngine`. Depositing extra collateral via `creditDeposit()` calls `spotEngine.updateBalance`, which directly raises the subaccount's recorded balance and therefore its health score.

An attacker who has a financial interest in keeping a specific subaccount alive (e.g., a counterparty with open perp positions against it, or the subaccount owner themselves acting through a second address) can spend a small amount of a registered token to push the target subaccount's health above zero, blocking the liquidation. The protocol's insurance fund and other depositors bear the resulting bad-debt risk if the position later becomes unrecoverable.

---

### Likelihood Explanation

- The entry point (`creditDeposit()` / `creditDepositV1()`) is reachable by any EOA or contract with no preconditions.
- The DDA address for any subaccount is publicly readable from `ContractOwner.directDepositV1Address`.
- The cost to the attacker is only the donated token amount, which can be minimal if the target subaccount is only slightly below the liquidation threshold.
- The attack is economically rational whenever the attacker's gain from blocking liquidation (e.g., avoiding a loss on a correlated position) exceeds the donated token cost.

---

### Recommendation

Restrict `creditDeposit()` to the DDA owner (i.e., `ContractOwner`) or to the subaccount owner:

```solidity
function creditDeposit() external onlyOwner { ... }
```

Since `ContractOwner` is already the `Ownable` owner of every DDA (set at deployment via `new DirectDepositV1{salt:...}(...)`), this change preserves the intended keeper-bot flow through `ContractOwner.creditDepositV1` while closing the permissionless inflation path.

---

### Proof of Concept

```
1. Target subaccount S has health = -1 (just below liquidation threshold).
2. Attacker reads ContractOwner.directDepositV1Address[S] → DDA address D.
3. Attacker calls USDC.transfer(D, 1e6)  // 1 USDC
4. Attacker calls D.creditDeposit()
   → token.balanceOf(D) = 1e6
   → endpoint.depositCollateralWithReferral(S, QUOTE_PRODUCT_ID, 1e6, "-1")
   → SpotEngine.updateBalance(QUOTE_PRODUCT_ID, S, +1e18)  // after multiplier
5. getHealth(S, MAINTENANCE) is now ≥ 0.
6. Any liquidation attempt for S reverts with ERR_SUBACCT_HEALTH.
7. S's bad debt accumulates; insurance fund absorbs the loss.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L64-67)
```text
    receive() external payable {
        (bool success, ) = wrappedNative.call{value: msg.value}("");
        require(success, "Failed to wrap native token.");
    }
```

**File:** core/contracts/DirectDepositV1.sol (L83-101)
```text
    function creditDeposit() external {
        uint32[] memory productIds = spotEngine.getProductIds();
        for (uint256 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            address tokenAddr = spotEngine.getToken(productId);
            require(tokenAddr != address(0), "Invalid productId.");
            IIERC20Base token = IIERC20Base(tokenAddr);
            uint256 balance = token.balanceOf(address(this));
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
            }
        }
    }
```

**File:** core/contracts/ContractOwner.sol (L38-38)
```text
    mapping(bytes32 => address payable) public directDepositV1Address;
```

**File:** core/contracts/ContractOwner.sol (L502-508)
```text
    function creditDepositV1(bytes32 subaccount) external {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        if (directDepositV1 == address(0)) {
            directDepositV1 = createDirectDepositV1(subaccount);
        }
        DirectDepositV1(directDepositV1).creditDeposit();
    }
```
