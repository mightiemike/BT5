### Title
Unchecked `transferFrom` Return Value Enables usdcE Drain from Direct Deposit Accounts — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` performs a raw `IERC20Base.transferFrom` call without checking its return value. The contract already imports and uses `ERC20Helper` (which wraps transfers with return-value checks) everywhere else, but this single call bypasses that wrapper. If the USDC token returns `false` instead of reverting on failure, the function continues and transfers usdcE out of the victim's Direct Deposit Account (DDA) to the caller for free.

---

### Finding Description

In `ContractOwner.sol`, the function `replaceUsdcEWithUsdc` is an `external` function with no access control beyond a chain-ID check (`block.chainid == 57073`):

```solidity
// core/contracts/ContractOwner.sol  lines 608–620
function replaceUsdcEWithUsdc(bytes32 subaccount) external {
    require(block.chainid == 57073, ERR_UNAUTHORIZED);
    ...
    uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
    if (balance > 0) {
        IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance); // ← unchecked
        DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
        IERC20Base(usdcE).safeTransfer(msg.sender, balance);
    }
}
``` [1](#0-0) 

The contract declares `using ERC20Helper for IERC20Base` at the top of the contract, and `ERC20Helper.safeTransferFrom` wraps the call in a low-level `call` and requires both `success == true` and a truthy decoded return value:

```solidity
// core/contracts/libraries/ERC20Helper.sol  lines 23–42
function safeTransferFrom(...) internal {
    (bool success, bytes memory data) = address(self).call(...);
    require(
        success && (data.length == 0 || abi.decode(data, (bool))),
        ERR_TRANSFER_FAILED
    );
}
``` [2](#0-1) 

Every other transfer in the codebase — `EndpointStorage.safeTransferFrom`, `BaseWithdrawPool.safeTransferFrom`, `Clearinghouse.handleWithdrawTransfer` — routes through `ERC20Helper`. The line at `ContractOwner.sol:616` is the sole exception. [3](#0-2) 

---

### Impact Explanation

The three-step sequence inside the `if (balance > 0)` block is:

1. **Pull USDC from caller into DDA** — `transferFrom(msg.sender, directDepositV1, balance)` (unchecked)
2. **Pull usdcE from DDA into ContractOwner** — `DirectDepositV1(directDepositV1).withdraw(usdcE)`
3. **Push usdcE to caller** — `safeTransfer(msg.sender, balance)`

If step 1 returns `false` (no revert), steps 2 and 3 still execute. The DDA never receives USDC, but its entire usdcE balance is transferred to the attacker. The corrupted asset delta is: attacker gains `balance` usdcE; the DDA subaccount loses `balance` usdcE; no USDC is deposited in exchange. [4](#0-3) 

---

### Likelihood Explanation

- The function is `external` with no `onlyOwner` or `onlyDeployer` guard — any EOA or contract on chain 57073 (Ink) can call it.
- The hardcoded USDC address (`0x2D270e6886d130D724215A266106e6832161EAEd`) is a non-standard deployment on Ink; its exact revert-vs-return-false behavior on insufficient allowance is not guaranteed to match mainnet USDC.
- An attacker needs only to identify a DDA with a non-zero usdcE balance and call the function without pre-approving USDC. If the token returns `false`, the drain succeeds silently. [5](#0-4) 

---

### Recommendation

Replace the raw `transferFrom` call with the `safeTransferFrom` wrapper already available via `ERC20Helper`:

```solidity
// Before (line 616)
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

This is consistent with every other transfer site in the protocol and will revert if the token returns `false` or the call itself fails. [6](#0-5) 

---

### Proof of Concept

1. Identify any `subaccount` whose DDA (`directDepositV1Address[subaccount]`) holds a non-zero usdcE balance.
2. On chain 57073, call `ContractOwner.replaceUsdcEWithUsdc(subaccount)` from an EOA that has **zero** USDC allowance granted to `ContractOwner`.
3. If `IERC20Base(usdc).transferFrom(attacker, dda, balance)` returns `false` (no revert):
   - `DirectDepositV1(dda).withdraw(usdcE)` transfers the full usdcE balance from the DDA to `ContractOwner`.
   - `IERC20Base(usdcE).safeTransfer(attacker, balance)` transfers that usdcE to the attacker.
4. Result: attacker receives `balance` usdcE; the DDA subaccount's collateral is permanently drained; no USDC was deposited. [7](#0-6)

### Citations

**File:** core/contracts/ContractOwner.sol (L24-24)
```text
    using ERC20Helper for IERC20Base;
```

**File:** core/contracts/ContractOwner.sol (L608-620)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
        address usdc = 0x2D270e6886d130D724215A266106e6832161EAEd;
        uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```
