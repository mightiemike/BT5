### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables Theft of USDC.e from DirectDepositV1 Contracts — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` uses a raw, unchecked `IERC20Base(usdc).transferFrom(...)` to pull USDC from the caller before releasing USDC.e. If the USDC token returns `false` on failure instead of reverting, the transfer silently fails and execution continues, allowing any caller to drain USDC.e from a `DirectDepositV1` contract without providing any USDC.

---

### Finding Description

`replaceUsdcEWithUsdc` is a migration helper callable by any external address on Ink Chain (chainid 57073). Its intended flow is:

1. Pull `balance` of USDC from `msg.sender` into `directDepositV1` (to replace the USDC.e held there).
2. Withdraw all USDC.e from `directDepositV1` to `ContractOwner`.
3. Send the USDC.e to `msg.sender`.

The critical step (1) uses a raw `transferFrom` call:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
``` [1](#0-0) 

This call's return value is never checked. The rest of the codebase — including the very next line — consistently uses `ERC20Helper.safeTransfer` / `safeTransferFrom`, which wrap the call and `require` a truthy return:

```solidity
IERC20Base(usdcE).safeTransfer(msg.sender, balance);
``` [2](#0-1) 

The `ERC20Helper` library's `safeTransferFrom` enforces the return value check:

```solidity
require(
    success && (data.length == 0 || abi.decode(data, (bool))),
    ERR_TRANSFER_FAILED
);
``` [3](#0-2) 

Because the raw `transferFrom` at line 616 is not wrapped, if the USDC token at the hardcoded address returns `false` (e.g., caller has zero allowance or zero balance), the failure is silently swallowed. Execution then proceeds to:

- `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` — which calls `safeTransfer` internally and moves all USDC.e from `directDepositV1` to `ContractOwner`.
- `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` — which sends all that USDC.e to the attacker. [4](#0-3) 

`DirectDepositV1.withdraw` is `onlyOwner`, and `ContractOwner` is the owner of every `DirectDepositV1` it deploys, so the withdrawal succeeds unconditionally once called from `ContractOwner`. [5](#0-4) 

---

### Impact Explanation

Any caller on Ink Chain (chainid 57073) with zero USDC allowance or balance can call `replaceUsdcEWithUsdc(subaccount)` for any subaccount whose `directDepositV1` holds USDC.e. The attacker receives the full USDC.e balance of that contract without providing any USDC. This is a direct, irreversible theft of user-deposited collateral tokens held in `DirectDepositV1` contracts.

---

### Likelihood Explanation

The function is `external` with no access control beyond the chain-id check. Any address on Ink Chain can call it. The only precondition is that a `directDepositV1` contract exists for the target subaccount and holds a non-zero USDC.e balance — both of which are normal operational states during the migration period this function was designed for. Likelihood is high if the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` on Ink Chain follows the return-false-on-failure pattern rather than reverting.

---

### Recommendation

Replace the raw `transferFrom` call with `ERC20Helper.safeTransferFrom`, consistent with every other token transfer in the codebase:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [6](#0-5) 

---

### Proof of Concept

1. A `directDepositV1` contract exists for `subaccount` and holds 1000 USDC.e.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` with zero USDC allowance.
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, 1000)` returns `false` — no revert.
4. `DirectDepositV1(directDepositV1).withdraw(usdcE)` transfers 1000 USDC.e to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, 1000)` sends 1000 USDC.e to the attacker.
6. Attacker receives 1000 USDC.e; `directDepositV1` receives 0 USDC. Net loss: 1000 USDC.e stolen from the subaccount's deposit contract. [7](#0-6)

### Citations

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

**File:** core/contracts/libraries/ERC20Helper.sol (L37-40)
```text

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
