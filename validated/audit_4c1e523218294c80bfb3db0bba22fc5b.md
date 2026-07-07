### Title
Unprivileged Caller Can Force-Deposit All Token Balances Into Hardcoded Subaccount via `creditDeposit()` — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.creditDeposit()` carries no access control modifier. Any external caller can invoke it at any time, causing the contract to sweep every supported token balance it holds and deposit the full amount into the hardcoded `subaccount` set at construction. This mirrors the GNTDeposit pattern: an unprivileged actor triggers a token-transfer path that was implicitly assumed to be owner-gated, producing an irreversible accounting corruption in the protocol's internal subaccount ledger.

---

### Finding Description

`DirectDepositV1` is a personal deposit proxy. A specific `subaccount` (bytes32) is baked in at construction time. The intended flow is: the subaccount owner sends tokens to this contract, then calls `creditDeposit()` to forward them into the protocol.

The function is declared `external` with no modifier:

```solidity
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
``` [1](#0-0) 

The asymmetry is telling: the sibling `withdraw()` function is `onlyOwner`, but `creditDeposit()` is open to the world. [2](#0-1) 

Any tokens sitting in the contract — regardless of who sent them or why — are swept into the hardcoded `subaccount` the moment any caller invokes `creditDeposit()`.

---

### Impact Explanation

**Broken invariant:** Tokens deposited by a user must be credited to *that user's* subaccount in the protocol's internal ledger (`SpotEngine` balance mapping). `creditDeposit()` unconditionally credits the hardcoded `subaccount`, not the actual sender.

**Concrete asset delta:**

1. User A accidentally sends tokens to User B's `DirectDepositV1` contract (a realistic mistake given that contract addresses are opaque bytes).
2. Any caller (including User B or a front-running bot) calls `creditDeposit()`.
3. `endpoint.depositCollateralWithReferral(subaccount, ...)` credits the full balance to User B's subaccount inside `SpotEngine`.
4. User A's tokens are permanently locked in User B's protocol balance. User A has no recovery path — the `withdraw()` function is `onlyOwner` (User B), and the protocol has no mechanism to reverse a completed deposit.

The result is an irreversible subaccount accounting corruption: User B's on-chain protocol balance is inflated by tokens that were never legitimately theirs, while User A suffers a permanent loss. [1](#0-0) 

---

### Likelihood Explanation

**Medium.** The trigger requires tokens to land in the wrong `DirectDepositV1` contract. This is realistic because:

- `DirectDepositV1` contracts are deployed per-subaccount; users interacting with multiple accounts or copy-pasting addresses can easily misdirect funds.
- The contract emits a `DirectDepositV1Created` event on construction, making all deployed instances discoverable on-chain by a monitoring attacker.
- No time window is needed — the attacker calls `creditDeposit()` immediately after observing the misdirected transfer in the mempool (front-run) or in a confirmed block. [3](#0-2) 

---

### Recommendation

Add an `onlyOwner` (or equivalent) modifier to `creditDeposit()`, consistent with the access control already applied to `withdraw()` and `withdrawNative()`. If the design intent is to allow permissionless forwarding, add a `sendTo` parameter so the caller can specify the destination subaccount, and validate that the caller is authorized to credit that subaccount. [4](#0-3) 

---

### Proof of Concept

1. User B deploys `DirectDepositV1` with their own `subaccount` bytes32.
2. User A mistakenly sends 10,000 USDC to User B's `DirectDepositV1` address.
3. Attacker (or User B) calls `DirectDepositV1.creditDeposit()`.
4. The contract calls `endpoint.depositCollateralWithReferral(subaccount_B, USDC_productId, 10000e6, "-1")`.
5. `SpotEngine` credits 10,000 USDC to `subaccount_B`.
6. User A's 10,000 USDC is permanently in User B's protocol balance. User A cannot recover it — `withdraw()` is `onlyOwner` (User B), and no protocol-level reversal exists. [1](#0-0)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L59-62)
```text
            }
        }
        emit DirectDepositV1Created(version(), subaccount, address(this));
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

**File:** core/contracts/DirectDepositV1.sol (L103-112)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }

    function withdrawNative() external onlyOwner {
        uint256 balance = address(this).balance;
        (bool success, ) = msg.sender.call{value: balance}("");
        require(success, "Failed to transfer native token to owner");
    }
```
