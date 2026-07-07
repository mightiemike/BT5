### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables Draining DDA usdcE Without Paying USDC — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(...)` without checking its return value. If the USDC token on chain 57073 (Ink mainnet) returns `false` on failure instead of reverting, the transfer silently fails. The function then continues to withdraw usdcE from the DDA and send it to the caller, meaning the caller receives usdcE tokens without having provided any USDC.

---

### Finding Description

`ContractOwner.replaceUsdcEWithUsdc` is a public function (no access control modifier) restricted only by `block.chainid == 57073`. Its logic is:

1. Read the usdcE balance held by a `DirectDepositV1` (DDA) contract.
2. Pull that amount of USDC from `msg.sender` into the DDA via `transferFrom`.
3. Withdraw usdcE from the DDA to `ContractOwner` via `DirectDepositV1.withdraw`.
4. Send the usdcE from `ContractOwner` to `msg.sender` via `safeTransfer`.

Step 2 uses a raw, unchecked call:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
``` [1](#0-0) 

The return value of `transferFrom` is never inspected. The `IERC20Base` interface declares `transferFrom` as returning `bool`: [2](#0-1) 

The project already implements a safe wrapper in `ERC20Helper.safeTransferFrom` that checks both the call success and the decoded boolean return value: [3](#0-2) 

This safe wrapper is used correctly everywhere else (e.g., `BaseWithdrawPool.safeTransferFrom`): [4](#0-3) 

But it is **not** used at line 616 of `ContractOwner.sol`.

---

### Impact Explanation

If USDC on chain 57073 returns `false` on a failed `transferFrom` (e.g., insufficient allowance or balance) instead of reverting, the call at line 616 silently succeeds from the EVM's perspective. Execution continues:

- `DirectDepositV1.withdraw(usdcE)` transfers the DDA's entire usdcE balance to `ContractOwner`.
- `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` sends that usdcE to the caller.

The caller receives usdcE tokens without having paid any USDC. The DDA — which holds collateral on behalf of a user's subaccount — is drained. The subaccount owner loses their usdcE, which was pending deposit into the protocol. [5](#0-4) 

---

### Likelihood Explanation

- The function has **no access control** — any address on chain 57073 can call it.
- The only precondition is that a DDA for the target `subaccount` exists and holds a non-zero usdcE balance.
- The attacker needs zero USDC allowance or balance to trigger the silent failure path.
- The DDA is a publicly known contract (address stored in `directDepositV1Address`), so balances are observable on-chain. [6](#0-5) 

---

### Recommendation

Replace the raw `transferFrom` call with the project's own `ERC20Helper.safeTransferFrom`, which is already imported and used throughout the codebase:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [3](#0-2) 

---

### Proof of Concept

1. A user's DDA holds 1000 usdcE (balance pending deposit).
2. Attacker calls `replaceUsdcEWithUsdc(subaccount)` with zero USDC allowance.
3. `IERC20Base(usdc).transferFrom(attacker, dda, 1000)` returns `false` — call does not revert, return value is ignored.
4. `DirectDepositV1(dda).withdraw(usdcE)` executes: 1000 usdcE moves from DDA → ContractOwner.
5. `IERC20Base(usdcE).safeTransfer(attacker, 1000)` executes: 1000 usdcE moves from ContractOwner → attacker.
6. Attacker holds 1000 usdcE; DDA is empty; subaccount owner's collateral is gone. [5](#0-4)

### Citations

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

**File:** core/contracts/interfaces/IERC20Base.sol (L25-29)
```text
    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) external returns (bool);
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

**File:** core/contracts/BaseWithdrawPool.sol (L192-198)
```text
    function safeTransferFrom(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal virtual {
        token.safeTransferFrom(from, address(this), amount);
    }
```
